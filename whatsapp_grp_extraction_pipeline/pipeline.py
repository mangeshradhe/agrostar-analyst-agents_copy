#!/usr/bin/env python3
"""
WhatsApp Group Extraction Pipeline — turns a COCO Training & Technical
Support WhatsApp chat export into a structured agricultural diagnosis
test-case dataset (Excel), cross-checked against an Issues V4 taxonomy.

Built from a manual run (Sep 2026) that repeatedly hit the same class of
bug: the plain-text .txt export drops WhatsApp's reply-to/quote links, so
when multiple farmers post images close together, naive "nearest response"
matching sometimes grabs the wrong training-team reply. This pipeline
bakes in the checks that caught those bugs by hand:
  - a duplicate-response-text scan (two different cases sharing byte-
    identical solution text is a strong signal one stole the other's reply)
  - an interleaving-risk scan (other farmers posting images in the same
    window as this case, before the matched response — the specific
    pattern that caused every bug found)
  - a `verify` stage that re-reads raw chat context for every flagged case
    via `claude -p` and fixes/drops/splits as needed

Everything is driven by headless `claude -p` calls (your existing Claude
Code login — no separate ANTHROPIC_API_KEY needed) for the judgment-heavy
extraction/verification steps, and plain deterministic Python for parsing,
clustering, scanning, V4 mapping, and building the final xlsx.

Usage:
    python3 pipeline.py run <chat_export.zip_or_dir> --v4 <IssuesV4.csv> [--workdir DIR]
    python3 pipeline.py run <chat_export.zip_or_dir> --v4 <IssuesV4.csv> --only parse,cluster,extract
    python3 pipeline.py run ... --s3-base "https://s3.ap-south-1.amazonaws.com/static.agrostar.in/static/Parchi+Testing+Images/"

Stages, in order: parse, cluster, extract, scan, verify, map-v4, build-xlsx.
Safe to re-run — each stage skips work whose output file already exists
(delete the relevant output file, or pass --only, to force a redo).
"""
import argparse
import csv
import difflib
import json
import os
import re
import sys
import zipfile
import datetime
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import openpyxl

from claude_cli import run_claude, ClaudeCliError
from prompts import extract_batch_prompt, verify_flagged_prompt

DEFAULT_S3_BASE = "https://s3.ap-south-1.amazonaws.com/static.agrostar.in/static/Parchi+Testing+Images/"

TRAINING_TEAM = {
    'Tarun Kumar', 'Pooja Training team', 'Jaya Training Team', 'Sujatha Bhambure Agrostar',
    'Yogesh Agrostar', 'Sachin Agrostar', 'Puneet Sethi Agrostar', 'Sunil Jain Agrostar',
    'Darpan Pathar Agrostar', 'Pankaj Jadhav Agrostar',
}

MSG_RE = re.compile(r'^(\d{1,2}/\d{1,2}/\d{2}), (\d{1,2}:\d{2}\s?[AP]M) - ([^:]+?): (.*)$')
SYS_RE = re.compile(r'^(\d{1,2}/\d{1,2}/\d{2}), (\d{1,2}:\d{2}\s?[AP]M) - (.*)$')
IMG_RE = re.compile(r'([\w\-\.]+\.(jpg|jpeg|png|webp))\s*\(file attached\)', re.I)
DOSE_RE = re.compile(r'\b(ml|gm|kg|litre|liter|spray|acre|pump|drench)\b', re.I)


def log(msg):
    print(f"[pipeline] {msg}", flush=True)


def out(workdir, name):
    return os.path.join(workdir, name)


def chunked(items, size):
    return [items[i:i + size] for i in range(0, len(items), size)]


def url_for(fn, s3_base):
    return s3_base + fn.replace(' ', '%20')


# ---------------------------------------------------------------------------
# Stage: parse
# ---------------------------------------------------------------------------

def find_chat_txt(export_path):
    """export_path may be a .zip, or an already-extracted directory."""
    if os.path.isdir(export_path):
        for f in os.listdir(export_path):
            if f.lower().endswith('.txt'):
                return os.path.join(export_path, f), export_path
        raise FileNotFoundError(f"no .txt chat file found in {export_path}")
    if export_path.lower().endswith('.zip'):
        extract_dir = export_path[:-4] + '_extracted'
        if not os.path.isdir(extract_dir):
            log(f"extracting {export_path} -> {extract_dir}")
            with zipfile.ZipFile(export_path) as z:
                z.extractall(extract_dir)
        return find_chat_txt(extract_dir)
    raise ValueError(f"expected a .zip or a directory, got: {export_path}")


def parse_dt(date, time):
    return datetime.datetime.strptime(f"{date} {time.replace(chr(0x202f), ' ').strip()}", "%m/%d/%y %I:%M %p")


def stage_parse(export_path, workdir):
    dest = out(workdir, 'messages.json')
    if os.path.exists(dest):
        log('parse: messages.json already exists, skipping (delete it to re-run)')
        with open(dest) as f:
            return json.load(f), find_chat_txt(export_path)[1]

    txt_path, media_dir = find_chat_txt(export_path)
    log(f'parse: reading {txt_path}')
    with open(txt_path, encoding='utf-8') as f:
        text = f.read()

    messages = []
    cur = None
    for line in text.split('\n'):
        m = MSG_RE.match(line)
        if m:
            if cur:
                messages.append(cur)
            date, time, sender, content = m.groups()
            cur = {'date': date, 'time': time, 'sender': sender.strip(), 'content': content}
            continue
        m2 = SYS_RE.match(line)
        if m2:
            if cur:
                messages.append(cur)
            date, time, content = m2.groups()
            cur = {'date': date, 'time': time, 'sender': None, 'content': content}
            continue
        if cur:
            cur['content'] += '\n' + line
    if cur:
        messages.append(cur)

    for i, m in enumerate(messages):
        m['idx'] = i
        try:
            m['dt'] = parse_dt(m['date'], m['time']).isoformat()
        except Exception:
            m['dt'] = None

    with open(dest, 'w') as f:
        json.dump(messages, f, indent=1)
    log(f'parse: {len(messages)} messages -> {dest}')
    return messages, media_dir


# ---------------------------------------------------------------------------
# Stage: cluster
# ---------------------------------------------------------------------------

def stage_cluster(messages, workdir):
    dest = out(workdir, 'clusters.json')
    if os.path.exists(dest):
        log('cluster: clusters.json already exists, skipping')
        with open(dest) as f:
            return json.load(f)

    def dt(m):
        return datetime.datetime.fromisoformat(m['dt']) if m['dt'] else None

    img_msgs = [m for m in messages if IMG_RE.search(m['content'])]
    farmer_img = [m for m in img_msgs if m['sender'] not in TRAINING_TEAM and m['sender'] is not None]

    clusters = []
    cur = None
    for m in farmer_img:
        if cur and cur['sender'] == m['sender'] and (dt(m) - dt(cur['last_msg'])).total_seconds() <= 600:
            cur['imgs'].append(m)
            cur['last_msg'] = m
        else:
            if cur:
                clusters.append({'sender': cur['sender'], 'imgs': cur['imgs']})
            cur = {'sender': m['sender'], 'imgs': [m], 'last_msg': m}
    if cur:
        clusters.append({'sender': cur['sender'], 'imgs': cur['imgs']})

    with open(dest, 'w') as f:
        json.dump(clusters, f, indent=1)
    log(f'cluster: {len(clusters)} candidate image-clusters -> {dest}')
    return clusters


# ---------------------------------------------------------------------------
# Stage: extract (claude -p, parallel batches)
# ---------------------------------------------------------------------------

def stage_extract(clusters, workdir, messages_path, jobs, chunk_size):
    dest = out(workdir, 'extracted.json')
    if os.path.exists(dest):
        log('extract: extracted.json already exists, skipping')
        with open(dest) as f:
            return json.load(f)

    batches = chunked(clusters, chunk_size)
    log(f'extract: {len(clusters)} clusters -> {len(batches)} batches of ~{chunk_size}, {jobs} parallel')

    def run_batch(i, batch):
        prompt = extract_batch_prompt(json.dumps(batch), messages_path)
        text = run_claude(prompt, allowed_tools=["Read", "Bash(python3:*)"], cwd=workdir, timeout=900)
        text = re.sub(r'^```(json)?|```$', '', text.strip(), flags=re.M).strip()
        try:
            cases = json.loads(text)
        except json.JSONDecodeError as e:
            log(f'  batch {i}: JSON parse failed ({e}); writing raw to batch_{i}_raw.txt for inspection')
            with open(out(workdir, f'batch_{i}_raw.txt'), 'w') as f:
                f.write(text)
            return []
        log(f'  batch {i}: {len(cases)} cases')
        return cases

    all_cases = []
    with ThreadPoolExecutor(max_workers=jobs) as ex:
        futs = {ex.submit(run_batch, i, b): i for i, b in enumerate(batches)}
        for fut in as_completed(futs):
            try:
                all_cases.extend(fut.result())
            except ClaudeCliError as e:
                log(f'  batch {futs[fut]} failed: {e}')

    with open(dest, 'w') as f:
        json.dump(all_cases, f, indent=1)
    log(f'extract: {len(all_cases)} total cases -> {dest}')
    return all_cases


# ---------------------------------------------------------------------------
# Stage: scan (deterministic — duplicate-response-text + interleaving-risk)
# ---------------------------------------------------------------------------

def solution_sig(case):
    txt = ' '.join(case.get('solution', [])).lower()
    txt = re.sub(r'[^a-z0-9]+', ' ', txt).strip()
    return txt


def stage_scan(cases, messages, workdir):
    dest = out(workdir, 'scan_report.json')

    # 1. duplicate-response-text groups
    groups = defaultdict(list)
    for i, c in enumerate(cases):
        s = solution_sig(c)
        if s and 'no specific product' not in s:
            groups[s].append(i)
    dup_groups = [idxs for idxs in groups.values() if len(idxs) > 1]

    # 2. interleaving-risk: other senders posting images before this case's
    #    matched response, within a 2h window
    img_idx = {}
    for m in messages:
        mm = IMG_RE.search(m['content'])
        if mm:
            img_idx[mm.group(1)] = m['idx']

    def dt(m):
        return datetime.datetime.fromisoformat(m['dt']) if m['dt'] else None

    risk = []
    for i, c in enumerate(cases):
        imgs = c.get('images', [])
        if not imgs:
            continue
        first_idx = img_idx.get(imgs[0])
        if first_idx is None:
            continue
        actual_sender = messages[first_idx]['sender']
        own_imgs = set(imgs)
        base_dt = dt(messages[first_idx])
        others = []
        j = first_idx + 1
        while j < len(messages) and j < first_idx + 400:
            m = messages[j]
            d = dt(m)
            if d and base_dt and (d - base_dt).total_seconds() > 2 * 3600:
                break
            if m['sender'] in TRAINING_TEAM and len(m['content']) > 15 and not IMG_RE.search(m['content']):
                break
            mm = IMG_RE.search(m['content'])
            if mm and mm.group(1) not in own_imgs and m['sender'] != actual_sender and m['sender'] not in TRAINING_TEAM:
                others.append({'sender': m['sender'], 'image': mm.group(1), 'idx': m['idx']})
            j += 1
        if others:
            risk.append({'case_idx': i, 'case': c, 'first_query_idx': first_idx, 'other_senders_images': others})

    report = {'duplicate_response_groups': [[cases[i] for i in idxs] for idxs in dup_groups],
              'duplicate_response_group_indices': dup_groups,
              'interleaving_risk': risk}
    with open(dest, 'w') as f:
        json.dump(report, f, indent=1)
    log(f'scan: {len(dup_groups)} duplicate-response groups, {len(risk)} interleaving-risk cases -> {dest}')
    return report


# ---------------------------------------------------------------------------
# Stage: verify (claude -p re-checks every flagged case against raw context)
# ---------------------------------------------------------------------------

def stage_verify(cases, report, workdir, messages_path, jobs, chunk_size):
    dest = out(workdir, 'verified.json')
    if os.path.exists(dest):
        log('verify: verified.json already exists, skipping')
        with open(dest) as f:
            return json.load(f)

    flagged_idx = set()
    for idxs in report['duplicate_response_group_indices']:
        flagged_idx.update(idxs)
    for r in report['interleaving_risk']:
        flagged_idx.add(r['case_idx'])

    if not flagged_idx:
        log('verify: nothing flagged, cases pass through unchanged')
        with open(dest, 'w') as f:
            json.dump(cases, f, indent=1)
        return cases

    log(f'verify: {len(flagged_idx)} flagged cases to re-check')
    flagged_list = []
    for i in sorted(flagged_idx):
        c = dict(cases[i])
        c['case_id'] = f'c{i}'
        flagged_list.append(c)

    batches = chunked(flagged_list, chunk_size)

    def run_batch(batch):
        prompt = verify_flagged_prompt(json.dumps(batch), messages_path)
        text = run_claude(prompt, allowed_tools=["Read"], cwd=workdir, timeout=900)
        text = re.sub(r'^```(json)?|```$', '', text.strip(), flags=re.M).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            log(f'  verify batch: JSON parse failed ({e})')
            return []

    actions = []
    with ThreadPoolExecutor(max_workers=jobs) as ex:
        futs = [ex.submit(run_batch, b) for b in batches]
        for fut in as_completed(futs):
            actions.extend(fut.result())

    by_id = {f'c{i}': i for i in flagged_idx}
    result = list(cases)
    drop_idx = set()
    for a in actions:
        act = a.get('action')
        if act == 'keep':
            continue
        if act == 'fix':
            i = by_id.get(a.get('case_id'))
            if i is not None:
                for k in ('images', 'symptoms', 'diagnosis', 'solution', 'source_expert', 'confidence'):
                    if k in a:
                        result[i][k] = a[k]
        elif act == 'drop':
            i = by_id.get(a.get('case_id'))
            if i is not None:
                drop_idx.add(i)
        elif act == 'add':
            new_case = {k: a[k] for k in ('images', 'symptoms', 'diagnosis', 'solution', 'source_expert', 'confidence', 'source_date') if k in a}
            result.append(new_case)

    result = [c for i, c in enumerate(result) if i not in drop_idx]
    log(f'verify: {len(drop_idx)} dropped, {len(result)} remain -> {dest}')
    with open(dest, 'w') as f:
        json.dump(result, f, indent=1)
    return result


# ---------------------------------------------------------------------------
# Stage: map-v4 (deterministic fuzzy match against Issues V4 taxonomy)
# ---------------------------------------------------------------------------

def load_v4(v4_csv):
    with open(v4_csv, encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    return [r['issueName'] for r in rows if r.get('issueName')]


def normalize(s):
    return re.sub(r'[^a-z0-9 ]', '', s.lower()).strip()


def best_v4_match(diagnosis_text, v4_names, threshold=0.72):
    norm_diag = normalize(diagnosis_text)
    diag_words = set(norm_diag.split())
    best, best_score = None, 0
    for name in v4_names:
        norm_name = normalize(name)
        name_words = set(norm_name.split())
        if not (diag_words & name_words):
            continue  # require actual keyword overlap, not just character similarity
        ratio = difflib.SequenceMatcher(None, norm_diag, norm_name).ratio()
        if ratio > best_score:
            best_score, best = ratio, name
    return best if best_score >= threshold else None


def stage_map_v4(cases, v4_csv, workdir):
    dest = out(workdir, 'mapped.json')
    if os.path.exists(dest):
        log('map-v4: mapped.json already exists, skipping')
        with open(dest) as f:
            return json.load(f)

    v4_names = load_v4(v4_csv)
    log(f'map-v4: {len(v4_names)} canonical issue names loaded')
    exact, fallback = 0, 0
    for c in cases:
        mapped = []
        for d in c.get('diagnosis', []):
            if d == 'Diagnosis not explicitly stated in the response.':
                mapped.append(d)
                continue
            m = best_v4_match(d, v4_names)
            if m:
                mapped.append(m)
                exact += 1
            else:
                mapped.append(d)
                fallback += 1
        c['diagnosis'] = mapped
    log(f'map-v4: {exact} matched to V4 canonical names, {fallback} kept as chat-derived phrasing')

    with open(dest, 'w') as f:
        json.dump(cases, f, indent=1)
    return cases


# ---------------------------------------------------------------------------
# Stage: build-xlsx
# ---------------------------------------------------------------------------

def stage_build_xlsx(cases, workdir, s3_base, out_path, drop_low_confidence, drop_blank_images):
    for c in cases:
        c.setdefault('s3_urls', [url_for(f, s3_base) for f in c.get('images', [])])
        c.setdefault('image_count', len(c.get('images', [])))

    if drop_blank_images:
        before = len(cases)
        cases = [c for c in cases if c.get('images')]
        log(f'build-xlsx: dropped {before - len(cases)} blank-image cases')
    if drop_low_confidence:
        before = len(cases)
        cases = [c for c in cases if c.get('confidence') != 'Low']
        log(f'build-xlsx: dropped {before - len(cases)} Low-confidence cases')

    cases.sort(key=lambda d: (d.get('source_date') or '9999-99-99', d.get('first_query_idx', 0) or 0))

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Test Cases"
    headers = ["TC ID", "Image Count", "Images (original files)", "S3 Image URLs",
               "Symptoms / User Input", "Expected Diagnosis", "Expected Solution",
               "Source Expert", "Confidence", "Source Date"]
    ws.append(headers)
    for i, d in enumerate(cases, start=1):
        ws.append([
            f"TC_{i:03d}", d.get('image_count', len(d.get('images', []))),
            json.dumps(d.get('images', [])), json.dumps(d.get('s3_urls', [])),
            d.get('symptoms', ''), json.dumps(d.get('diagnosis', [])), json.dumps(d.get('solution', [])),
            d.get('source_expert', ''), d.get('confidence', ''), d.get('source_date', ''),
        ])
    widths = [10, 12, 40, 45, 40, 30, 45, 18, 12, 14]
    for col, w in enumerate(widths, start=1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(col)].width = w
    wb.save(out_path)
    log(f'build-xlsx: {len(cases)} cases -> {out_path}')
    log(f'  confidence breakdown: {dict(Counter(d.get("confidence") for d in cases))}')
    return cases


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

ALL_STAGES = ['parse', 'cluster', 'extract', 'scan', 'verify', 'map-v4', 'build-xlsx']


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)

    run = sub.add_parser('run', help='run the pipeline')
    run.add_argument('chat_export', help='.zip of the WhatsApp chat export, or an already-extracted directory')
    run.add_argument('--v4', required=True, help='path to the Issues V4 issueProducts.csv')
    run.add_argument('--workdir', default='.', help='directory for intermediate/output files')
    run.add_argument('--out', default=None, help='output xlsx path (default: <workdir>/test_cases.xlsx)')
    run.add_argument('--s3-base', default=DEFAULT_S3_BASE)
    run.add_argument('--jobs', type=int, default=6, help='parallel `claude -p` calls')
    run.add_argument('--chunk-size', type=int, default=25, help='clusters per claude -p batch')
    run.add_argument('--only', default=None, help='comma-separated stages to run, e.g. parse,cluster')
    run.add_argument('--keep-low-confidence', action='store_true', help='keep Low-confidence cases in the final xlsx (default: dropped)')
    run.add_argument('--keep-blank-images', action='store_true', help='keep cases with no recoverable images in the final xlsx (default: dropped)')

    args = p.parse_args()
    if args.cmd != 'run':
        p.print_help()
        sys.exit(1)

    os.makedirs(args.workdir, exist_ok=True)
    stages = args.only.split(',') if args.only else ALL_STAGES

    messages, media_dir = stage_parse(args.chat_export, args.workdir) if 'parse' in stages else (
        json.load(open(out(args.workdir, 'messages.json'))), None)
    messages_path = os.path.abspath(out(args.workdir, 'messages.json'))

    clusters = stage_cluster(messages, args.workdir) if 'cluster' in stages else \
        json.load(open(out(args.workdir, 'clusters.json')))

    cases = stage_extract(clusters, args.workdir, messages_path, args.jobs, args.chunk_size) if 'extract' in stages else \
        json.load(open(out(args.workdir, 'extracted.json')))

    if 'scan' in stages:
        report = stage_scan(cases, messages, args.workdir)
    else:
        report = json.load(open(out(args.workdir, 'scan_report.json')))

    cases = stage_verify(cases, report, args.workdir, messages_path, args.jobs, args.chunk_size) if 'verify' in stages else \
        json.load(open(out(args.workdir, 'verified.json')))

    cases = stage_map_v4(cases, args.v4, args.workdir) if 'map-v4' in stages else \
        json.load(open(out(args.workdir, 'mapped.json')))

    if 'build-xlsx' in stages:
        out_path = args.out or out(args.workdir, 'test_cases.xlsx')
        stage_build_xlsx(cases, args.workdir, args.s3_base, out_path,
                          drop_low_confidence=not args.keep_low_confidence,
                          drop_blank_images=not args.keep_blank_images)

    log('done.')


if __name__ == '__main__':
    main()
