#!/usr/bin/env python3
"""
AgroStar POS — AMP for Email Newsletter
Uploads screenshots to Google Drive (org-restricted), then creates a Gmail
draft with a proper AMP MIME structure so the carousel works inside Gmail.

Run once: it will open a browser for OAuth, then do everything automatically.
"""

import os
import json
import base64
import mimetypes
import warnings
warnings.filterwarnings('ignore')

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ── CONFIG ──────────────────────────────────────────────────────────────────
SCOPES = [
    'https://www.googleapis.com/auth/gmail.compose',
    'https://www.googleapis.com/auth/drive.file',
]
TOKEN_FILE   = '/tmp/pos_newsletter_token.json'
GCLOUD_CREDS = os.path.expanduser('~/.config/gcloud/application_default_credentials.json')
CACHE_DIR    = '/Users/darpan/.claude/image-cache/2a63062f-09d6-41bc-bdb8-18dd263125e9'
ORG_DOMAIN   = 'agrostar.in'
TO_EMAIL     = 'Darpan.pathar@agrostar.in'
SUBJECT      = 'One screen. Seven stores. ₹73,824 in 20 days. Introducing AgroStar POS.'

IMAGES = [
    ('10.jpeg', 'Login — secure OTP, store assigned'),
    ('9.png',   'Home — live offers, store at a glance'),
    ('6.png',   'Farmer Search — profile in one tap'),
    ('5.png',   'Inventory — stock levels, request transfers'),
    ('7.png',   'Stock Transfers — track every inbound shipment'),
    ('8.png',   'Orders — walk-in & DVS, one screen'),
    ('3.png',   'Hisaab — outstanding balance & VAN details'),
    ('2.png',   'Transactions — every sale, every settlement'),
]

# ── AUTH ─────────────────────────────────────────────────────────────────────
def get_credentials():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            with open(GCLOUD_CREDS) as f:
                gc = json.load(f)
            client_config = {"installed": {
                "client_id":     gc['client_id'],
                "client_secret": gc['client_secret'],
                "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
                "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
                "token_uri":     "https://oauth2.googleapis.com/token",
            }}
            flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
            creds = flow.run_local_server(port=8080, open_browser=True)
        with open(TOKEN_FILE, 'w') as f:
            f.write(creds.to_json())
    return creds

# ── DRIVE UPLOAD ─────────────────────────────────────────────────────────────
def upload_image(drive, filepath, name):
    mime, _ = mimetypes.guess_type(filepath)
    meta = {'name': name}
    media = MediaFileUpload(filepath, mimetype=mime)
    f = drive.files().create(body=meta, media_body=media, fields='id').execute()
    fid = f['id']
    drive.permissions().create(
        fileId=fid,
        body={'type': 'domain', 'role': 'reader', 'domain': ORG_DOMAIN},
        sendNotificationEmail=False
    ).execute()
    return fid

def drive_url(fid):
    return f'https://drive.google.com/uc?export=view&id={fid}'

# ── AMP HTML ─────────────────────────────────────────────────────────────────
AMP_BOILERPLATE = (
    'body{-webkit-animation:-amp-start 8s steps(1,end) 0s 1 normal both;'
    '-moz-animation:-amp-start 8s steps(1,end) 0s 1 normal both;'
    '-ms-animation:-amp-start 8s steps(1,end) 0s 1 normal both;'
    'animation:-amp-start 8s steps(1,end) 0s 1 normal both}'
    '@-webkit-keyframes -amp-start{from{visibility:hidden}to{visibility:visible}}'
    '@-moz-keyframes -amp-start{from{visibility:hidden}to{visibility:visible}}'
    '@-ms-keyframes -amp-start{from{visibility:hidden}to{visibility:visible}}'
    '@keyframes -amp-start{from{visibility:hidden}to{visibility:visible}}'
)

def build_amp(image_urls, captions):
    slides = '\n'.join(
        f'    <amp-img src="{url}" width="175" height="330" layout="fixed" '
        f'style="border-radius:14px;border:2px solid rgba(255,255,255,0.15);" '
        f'alt="{cap}"></amp-img>'
        for url, cap in zip(image_urls, captions)
    )

    return f'''<!doctype html>
<html ⚡4email data-css-strict>
<head>
  <meta charset="utf-8">
  <script async src="https://cdn.ampproject.org/v0.js"></script>
  <script async custom-element="amp-carousel"
          src="https://cdn.ampproject.org/v0/amp-carousel-0.1.js"></script>
  <style amp4email-boilerplate>{AMP_BOILERPLATE}</style>
  <noscript><style amp4email-boilerplate>body{{-webkit-animation:none;-moz-animation:none;-ms-animation:none;animation:none}}</style></noscript>
  <style amp-custom>
    body{{margin:0;padding:16px 0;background:#e8e8e8;
         font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif}}
    .w{{max-width:620px;margin:0 auto;background:#fff;border-radius:6px;overflow:hidden}}
    .hdr{{background:#8B1A1A;padding:14px 28px}}
    .hdr-brand{{color:#fff;font-size:15px;font-weight:700}}
    .hdr-sub{{color:rgba(255,255,255,0.6);font-size:11px}}
    .aim{{background:#7a1717;padding:14px 28px 18px;border-top:1px solid rgba(255,255,255,0.12)}}
    .aim-lbl{{font-size:9px;font-weight:700;letter-spacing:2.5px;text-transform:uppercase;
              color:rgba(255,255,255,0.5);margin-bottom:5px}}
    .aim-txt{{font-size:15px;font-weight:600;color:#fff;line-height:1.5}}
    .car-wrap{{background:#111;padding:18px 0 14px;text-align:center}}
    .car-lbl{{font-size:9px;font-weight:700;letter-spacing:2.5px;text-transform:uppercase;
              color:rgba(255,255,255,0.35);margin-bottom:14px}}
    .body{{padding:0 28px}}
    .kicker{{font-size:10px;font-weight:700;letter-spacing:2px;text-transform:uppercase;
             color:#8B1A1A;margin:24px 0 10px}}
    .headline{{font-size:30px;font-weight:700;line-height:1.15;color:#111;
               margin-bottom:12px;letter-spacing:-0.5px}}
    .subline{{font-size:15px;color:#555;line-height:1.65;padding-bottom:16px;
              border-bottom:1px solid #f0f0f0}}
    .tldr{{background:#FFF8E1;border-left:4px solid #FFA000;padding:14px 18px;margin:14px 0}}
    .tldr-lbl{{font-size:9px;font-weight:700;letter-spacing:2px;text-transform:uppercase;
               color:#FFA000;margin-bottom:8px}}
    .tldr-row{{font-size:13px;color:#333;line-height:1.6;margin-bottom:4px}}
    .nums-lbl{{font-size:9px;font-weight:700;letter-spacing:2px;text-transform:uppercase;
               color:#999;margin:14px 0 10px}}
    .nums{{border-collapse:collapse;width:100%}}
    .num-cell{{background:#fff;padding:14px 8px;text-align:center;border:1px solid #e8e8e8}}
    .num-val{{font-size:24px;font-weight:700;color:#8B1A1A;line-height:1}}
    .num-lbl{{font-size:10px;color:#888;margin-top:4px;line-height:1.3}}
    .divider{{height:1px;background:#f0f0f0;margin:18px 0}}
    .mom-num{{font-size:9px;font-weight:700;letter-spacing:2px;text-transform:uppercase;
              color:#8B1A1A;margin-bottom:6px}}
    .mom-title{{font-size:19px;font-weight:700;color:#111;line-height:1.25;margin-bottom:10px}}
    .mom-body{{font-size:14px;color:#444;line-height:1.7;margin-bottom:12px}}
    .feats{{background:#fafafa;border:1px solid #f0f0f0}}
    .feat{{padding:9px 14px;font-size:13px;color:#444;line-height:1.5;
           border-bottom:1px solid #f0f0f0}}
    .feat-last{{padding:9px 14px;font-size:13px;color:#444;line-height:1.5}}
    .arch{{background:#f9f9f9;border:1px solid #eee;padding:18px;margin-bottom:18px}}
    .arch-lbl{{font-size:9px;font-weight:700;letter-spacing:2px;text-transform:uppercase;
               color:#999;margin-bottom:10px}}
    .arch-txt{{font-size:14px;color:#333;line-height:1.7;margin-bottom:14px}}
    .arch-box{{background:#fff;border:1px solid #ddd;padding:8px 10px;text-align:center}}
    .arch-ctr{{background:#8B1A1A;padding:10px 14px;text-align:center}}
    .tbl-hdr{{background:#8B1A1A;color:#fff;padding:8px 12px;
              font-size:10px;font-weight:600;text-align:left}}
    .tbl-cell{{padding:8px 12px;font-size:13px;color:#333;border-bottom:1px solid #f0f0f0}}
    .tbl-val{{padding:8px 12px;font-size:13px;color:#8B1A1A;font-weight:600;
              border-bottom:1px solid #f0f0f0}}
    .coming{{background:#E3F2FD;padding:16px 18px;margin-bottom:18px}}
    .coming-lbl{{font-size:9px;font-weight:700;letter-spacing:2px;text-transform:uppercase;
                 color:#1565C0;margin-bottom:6px}}
    .coming-txt{{font-size:14px;color:#1a3a5c;line-height:1.6}}
    .kudos{{border-left:3px solid #8B1A1A;padding-left:16px;margin-bottom:24px}}
    .kudos-lbl{{font-size:9px;font-weight:700;letter-spacing:2px;text-transform:uppercase;
                color:#999;margin-bottom:7px}}
    .kudos-txt{{font-size:13px;color:#444;line-height:1.7}}
    .footer{{background:#f5f5f5;border-top:1px solid #e8e8e8;padding:14px 28px;
             font-size:11px;color:#aaa;line-height:1.6}}
  </style>
</head>
<body>
<div class="w">

  <div class="hdr">
    <div class="hdr-brand">&#127807; AgroStar</div>
    <div class="hdr-sub">Tech &amp; Product &nbsp;·&nbsp; June 2026 &nbsp;·&nbsp; POS Launch Edition</div>
  </div>

  <div class="aim">
    <div class="aim-lbl">AIM</div>
    <div class="aim-txt">Give every AgroStar store manager one screen to run their entire store — stock, sales, payments, and reconciliation — without touching any other system.</div>
  </div>

  <div class="car-wrap">
    <div class="car-lbl">AGROSTAR POS — IN ACTION</div>
    <amp-carousel width="620" height="360" layout="fixed"
                  type="carousel" autoplay delay="3500" loop>
{slides}
    </amp-carousel>
  </div>

  <div class="body">
    <div class="kicker">New Product &nbsp;·&nbsp; June 2026</div>
    <div class="headline">The complete store,<br>on one screen.</div>
    <div class="subline">AgroStar POS is built from the ground up to run our Company Owned stores — connecting orders, inventory, and finance into a single mobile-first experience. When a store opens, POS opens. That's it.</div>

    <div class="tldr">
      <div class="tldr-lbl">TL;DR — 30 seconds</div>
      <div class="tldr-row">&#8594; &nbsp;We built AgroStar POS — a purpose-built mobile app to run our 7 Company Owned stores end-to-end.</div>
      <div class="tldr-row">&#8594; &nbsp;One screen replaces three enterprise systems: order management, warehouse, and finance — all talking to each other in real time.</div>
      <div class="tldr-row">&#8594; &nbsp;Live since May 15. 65 orders. 57 farmers served. &#8377;73,824 in sales. Growing every week.</div>
    </div>

    <div class="nums-lbl">By the Numbers — Since May 15, 2026</div>
    <table class="nums" cellpadding="0" cellspacing="1" style="background:#e0e0e0;">
      <tr>
        <td class="num-cell"><div class="num-val">7</div><div class="num-lbl">COCO<br>Stores Live</div></td>
        <td class="num-cell"><div class="num-val">65</div><div class="num-lbl">Orders<br>Placed</div></td>
        <td class="num-cell"><div class="num-val">57</div><div class="num-lbl">Farmers<br>Served</div></td>
        <td class="num-cell"><div class="num-val" style="font-size:20px;">&#8377;73,824</div><div class="num-lbl">GMV in<br>20 Days</div></td>
      </tr>
    </table>

    <div class="divider"></div>

    <div class="mom-num">Magic Moment 01</div>
    <div class="mom-title">The stock arrives.<br>The store is ready in minutes.</div>
    <div class="mom-body">A truck pulls up. The store manager opens POS on their phone. They tap through incoming inventory — <strong>batch by batch, good or damaged</strong> — and hit confirm. The warehouse system updates automatically. No calls. No email. No waiting.</div>
    <div class="feats">
      <div class="feat">&#128230; &nbsp;Raise stock requests from the store — auto-converts to a warehouse transfer order</div>
      <div class="feat">&#9989; &nbsp;Receive incoming stock with good/bad capture per batch — warehouse updates instantly</div>
      <div class="feat-last">&#128260; &nbsp;Mark damaged inventory — moves to the correct bin in the warehouse system automatically</div>
    </div>

    <div class="divider"></div>

    <div class="mom-num">Magic Moment 02</div>
    <div class="mom-title">A farmer walks in.<br>Billed and out in under 3 minutes.</div>
    <div class="mom-body">The manager searches the farmer by mobile. <strong>Profile appears instantly.</strong> The farmer browses live prices, active offers, real stock. They pick what they need. The system selects the right batch automatically. <strong>Invoice generated. Payment collected</strong> — cash or a QR code on screen. The ledger reconciles itself.</div>
    <div class="feats">
      <div class="feat">&#128269; &nbsp;Search any farmer by mobile — or register a new one instantly</div>
      <div class="feat">&#128722; &nbsp;Full catalog with live prices, offers, stock count — same data as the farmer app</div>
      <div class="feat">&#129534; &nbsp;Invoice pushed to financial system automatically — zero manual entry</div>
      <div class="feat">&#128179; &nbsp;Cash (to store VAN) or dynamic UPI QR — both reconcile to the ledger automatically</div>
      <div class="feat-last">&#8617;&#65039; &nbsp;Returns on the same screen — condition captured, credit posted automatically</div>
    </div>

    <div class="divider"></div>

    <div class="mom-num">Magic Moment 03</div>
    <div class="mom-title">An online order just came in.<br>The store handles it.</div>
    <div class="mom-body">A farmer in the store's catchment placed an order on the AgroStar app. It routes directly to the nearest COCO store. The manager sees it in POS, <strong>marks it available and triggers the invoice in one tap.</strong> Returns handled on the same screen — logged, inventory updated, credit posted.</div>
    <div class="feats">
      <div class="feat">&#128242; &nbsp;DVS orders routed to the store appear directly in POS — accept, flag OOS, or process</div>
      <div class="feat">&#128666; &nbsp;Invoice sent to warehouse and logistics automatically at dispatch</div>
      <div class="feat-last">&#128260; &nbsp;Out-of-stock items trigger auto-restock — same engine as the Saathi partner network</div>
    </div>

    <div class="divider"></div>

    <div class="arch">
      <div class="arch-lbl">Under the Hood</div>
      <div class="arch-txt">POS is not a billing app. It is the <strong>connective layer</strong> between three systems that had never spoken to each other in a retail context. When a sale happens on POS, all three know about it <strong>instantly and automatically.</strong></div>
      <table width="100%" cellpadding="0" cellspacing="0"><tr>
        <td style="text-align:center;width:21%;"><div class="arch-box"><div style="font-size:11px;font-weight:700;color:#8B1A1A;">CRM</div><div style="font-size:9px;color:#888;">Orders &amp; Pricing</div></div></td>
        <td style="text-align:center;font-size:16px;color:#ccc;width:5%;">&#8644;</td>
        <td style="text-align:center;width:28%;"><div class="arch-ctr"><div style="font-size:11px;font-weight:700;color:#fff;">AgroStar POS</div><div style="font-size:9px;color:rgba(255,255,255,0.6);">The Store OS</div></div></td>
        <td style="text-align:center;font-size:16px;color:#ccc;width:5%;">&#8644;</td>
        <td style="text-align:center;width:21%;"><div class="arch-box"><div style="font-size:11px;font-weight:700;color:#8B1A1A;">WMS</div><div style="font-size:9px;color:#888;">Inventory</div></div></td>
        <td style="text-align:center;font-size:16px;color:#ccc;width:5%;">&#8644;</td>
        <td style="text-align:center;width:21%;"><div class="arch-box"><div style="font-size:11px;font-weight:700;color:#8B1A1A;">NAV</div><div style="font-size:9px;color:#888;">Finance</div></div></td>
      </tr></table>
    </div>

    <div class="nums-lbl">Store Performance — May 15 to June 3, 2026</div>
    <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;margin-bottom:18px;">
      <tr><th class="tbl-hdr">Store</th><th class="tbl-hdr">Orders</th><th class="tbl-hdr">Farmers</th><th class="tbl-hdr">GMV</th></tr>
      <tr style="background:#fff;"><td class="tbl-cell">COCO Pargaon Tarf Ale</td><td class="tbl-val">13</td><td class="tbl-val">13</td><td class="tbl-val">&#8377;15,018</td></tr>
      <tr style="background:#fafafa;"><td class="tbl-cell">COCO Narayangaon</td><td class="tbl-val">10</td><td class="tbl-val">8</td><td class="tbl-val">&#8377;13,856</td></tr>
      <tr style="background:#fff;"><td class="tbl-cell">COCO Manchar</td><td class="tbl-val">12</td><td class="tbl-val">8</td><td class="tbl-val">&#8377;12,848</td></tr>
      <tr style="background:#fafafa;"><td class="tbl-cell">COCO Kalamb</td><td class="tbl-val">7</td><td class="tbl-val">7</td><td class="tbl-val">&#8377;10,371</td></tr>
      <tr style="background:#fff;"><td class="tbl-cell">COCO Malegaon</td><td class="tbl-val">9</td><td class="tbl-val">8</td><td class="tbl-val">&#8377;7,967</td></tr>
      <tr style="background:#fafafa;"><td class="tbl-cell">COCO Sansar</td><td class="tbl-val">4</td><td class="tbl-val">4</td><td class="tbl-val">&#8377;7,507</td></tr>
      <tr style="background:#fff;"><td class="tbl-cell" style="border-bottom:none;">COCO Sangavi</td><td style="padding:8px 12px;font-size:13px;color:#8B1A1A;font-weight:600;">10</td><td style="padding:8px 12px;font-size:13px;color:#8B1A1A;font-weight:600;">9</td><td style="padding:8px 12px;font-size:13px;color:#8B1A1A;font-weight:600;">&#8377;6,257</td></tr>
    </table>

    <div class="coming">
      <div class="coming-lbl">Coming Next</div>
      <div class="coming-txt">Each COCO store will soon run its own promotions — independent pricing and store-level offers, so a store manager in Narayangaon can serve exactly what that village's farmers need, without waiting for a central catalog change.</div>
    </div>

    <div class="kudos">
      <div class="kudos-lbl">The People Behind It</div>
      <div class="kudos-txt"><strong>Rincy Rajan</strong> held the product vision across every system boundary — from a farmer's first tap to the last financial posting. <strong>Praveen Kumar Mahto</strong> built the full-stack architecture that made three enterprise systems work as one, on a mobile browser, for a store manager in a village. This is what AgroStar's retail future is built on.</div>
    </div>
  </div>

  <div class="footer">
    <strong>Darpan Pathar</strong> &nbsp;·&nbsp; Tech &amp; Product, AgroStar &nbsp;·&nbsp; Numbers pulled live from our data warehouse &nbsp;·&nbsp; June 4, 2026
  </div>

</div>
</body>
</html>'''


# ── HTML FALLBACK (non-AMP clients) ──────────────────────────────────────────
HTML_FALLBACK = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:16px 0;background:#e8e8e8;
             font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
<table width="620" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:6px;overflow:hidden;">
  <tr><td style="background:#8B1A1A;padding:14px 28px;">
    <div style="color:#fff;font-size:15px;font-weight:700;">&#127807; AgroStar &mdash; Tech &amp; Product</div>
    <div style="color:rgba(255,255,255,0.6);font-size:11px;">June 2026 &nbsp;&middot;&nbsp; POS Launch Edition</div>
  </td></tr>
  <tr><td style="background:#7a1717;padding:14px 28px 18px;">
    <div style="font-size:9px;font-weight:700;letter-spacing:2.5px;text-transform:uppercase;color:rgba(255,255,255,0.5);margin-bottom:5px;">AIM</div>
    <div style="font-size:15px;font-weight:600;color:#fff;line-height:1.5;">Give every AgroStar store manager one screen to run their entire store — stock, sales, payments, and reconciliation — without touching any other system.</div>
  </td></tr>
  <tr><td style="padding:24px 28px 16px;border-bottom:1px solid #f0f0f0;">
    <div style="font-size:10px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#8B1A1A;margin-bottom:10px;">New Product &middot; June 2026</div>
    <div style="font-size:30px;font-weight:700;line-height:1.15;color:#111;margin-bottom:12px;letter-spacing:-0.5px;">The complete store,<br>on one screen.</div>
    <div style="font-size:15px;color:#555;line-height:1.65;">AgroStar POS is built from the ground up to run our Company Owned stores. When a store opens, POS opens. That's it.</div>
  </td></tr>
  <tr><td style="padding:14px 28px;background:#FFF8E1;border-left:4px solid #FFA000;">
    <div style="font-size:9px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#FFA000;margin-bottom:8px;">TL;DR</div>
    <div style="font-size:13px;color:#333;line-height:1.6;">&#8594; 7 COCO stores live since May 15 &nbsp;&middot;&nbsp; 65 orders &nbsp;&middot;&nbsp; 57 farmers served &nbsp;&middot;&nbsp; &#8377;73,824 GMV<br>
    &#8594; One screen = order management + warehouse + finance, all talking in real time<br>
    &#8594; Built from scratch specifically for AgroStar&apos;s Company Owned stores</div>
  </td></tr>
  <tr><td style="padding:14px 28px;font-size:13px;color:#555;line-height:1.7;">
    Open this email in Gmail to see the full interactive version with live app screenshots and carousel.
  </td></tr>
  <tr><td style="background:#f5f5f5;border-top:1px solid #e8e8e8;padding:14px 28px;font-size:11px;color:#aaa;">
    <strong style="color:#888;">Darpan Pathar</strong> &middot; Tech &amp; Product, AgroStar &middot; June 4, 2026
  </td></tr>
</table>
</td></tr></table>
</body></html>"""

PLAIN_TEXT = """AgroStar POS Launch — June 2026
================================

AIM: Give every AgroStar store manager one screen to run their entire store — without touching any other system.

TL;DR:
- 7 COCO stores live since May 15
- 65 orders placed, 57 farmers served, Rs.73,824 GMV in 20 days
- One screen replaces CRM + WMS + NAV in a retail context

The complete store, on one screen.

Magic Moment 01: The stock arrives. The store is ready in minutes.
Magic Moment 02: A farmer walks in. Billed and out in under 3 minutes.
Magic Moment 03: An online order just came in. The store handles it.

People behind it: Rincy Rajan (Product) + Praveen Kumar Mahto (Engineering)

—
Darpan Pathar · Tech & Product, AgroStar · June 4, 2026
"""


# ── GMAIL DRAFT ───────────────────────────────────────────────────────────────
def create_draft(gmail, amp_html, html_fb, plain):
    msg = MIMEMultipart('alternative')
    msg['to']      = TO_EMAIL
    msg['subject'] = SUBJECT
    msg['from']    = 'me'
    # Order: plain → html → amp  (last preferred by Gmail)
    msg.attach(MIMEText(plain,   'plain',    'utf-8'))
    msg.attach(MIMEText(html_fb, 'html',     'utf-8'))
    msg.attach(MIMEText(amp_html,'x-amp-html','utf-8'))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    draft = gmail.users().drafts().create(
        userId='me', body={'message': {'raw': raw}}
    ).execute()
    return draft['id']


# ── MAIN ──────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print('\n=== AgroStar POS AMP Newsletter ===\n')

    print('Step 1/4  Authenticating with Google...')
    creds = get_credentials()
    drive = build('drive', 'v3', credentials=creds)
    gmail = build('gmail', 'v1', credentials=creds)
    print('          ✓ Connected\n')

    print('Step 2/4  Uploading screenshots to Google Drive (org-restricted)...')
    image_urls = []
    captions   = []
    for fname, caption in IMAGES:
        fpath = os.path.join(CACHE_DIR, fname)
        fid   = upload_image(drive, fpath, f'AgroStar_POS_{fname}')
        url   = drive_url(fid)
        image_urls.append(url)
        captions.append(caption)
        print(f'          ✓ {caption[:45]:<45}  id={fid}')
    print()

    print('Step 3/4  Building AMP email...')
    amp_html = build_amp(image_urls, captions)
    print(f'          ✓ AMP HTML built ({len(amp_html)//1024} KB)\n')

    print('Step 4/4  Creating Gmail draft...')
    draft_id = create_draft(gmail, amp_html, HTML_FALLBACK, PLAIN_TEXT)
    print(f'          ✓ Draft created — ID: {draft_id}\n')

    print('=== Done! ===')
    print('Open Gmail Drafts, add your org distribution list, and send.')
    print('AMP carousel will be live for all @agrostar.in recipients in Gmail.\n')
