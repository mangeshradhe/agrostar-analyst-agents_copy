#!/usr/bin/env python3
"""
AgroStar POS Newsletter v2 — Gmail Draft Creator
Uploads 6 screenshots to Google Drive (org-restricted), replaces local paths
in pos_newsletter_v2.html, then creates a Gmail draft.
"""

import os, json, base64, mimetypes, warnings
warnings.filterwarnings('ignore')

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ── CONFIG ──────────────────────────────────────────────────────────────────
SCOPES = [
    'https://www.googleapis.com/auth/gmail.compose',
    'https://www.googleapis.com/auth/drive.file',
]
TOKEN_FILE   = '/tmp/pos_newsletter_token.json'
GCLOUD_CREDS = os.path.expanduser('~/.config/gcloud/application_default_credentials.json')
HTML_FILE    = '/Users/darpan/Documents/claude code/DVS Analysis/pos_newsletter_v2.html'
TO_EMAIL     = 'Darpan.pathar@agrostar.in'
SUBJECT      = 'AgroStar POS — The Operating System. Built to run AgroStar\'s Retail at scale.'

# local path → Drive filename
IMAGES = [
    ('/Users/darpan/Desktop/Store Inventory.png',         'pos_v2_store_inventory.png'),
    ('/Users/darpan/Desktop/Farmer Search.png',           'pos_v2_farmer_search.png'),
    ('/Users/darpan/Desktop/Farmer and DVS orders.png',   'pos_v2_farmer_dvs_orders.png'),
    ('/Users/darpan/Desktop/View STO.png',                'pos_v2_view_sto.png'),
    ('/Users/darpan/Desktop/Inward STO.png',              'pos_v2_inward_sto.png'),
    ('/Users/darpan/.claude/image-cache/ded68070-1346-4645-a52e-36ff523e7b54/14.png', 'pos_v2_checkout.png'),
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
    # org-restricted sharing (agrostar.in only)
    drive.permissions().create(
        fileId=fid,
        body={'type': 'domain', 'role': 'reader', 'domain': 'agrostar.in'},
    ).execute()
    cdn_url = f'https://drive.google.com/uc?export=view&id={fid}'
    print(f'  ✓ {name} → {cdn_url}')
    return cdn_url

# ── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    print('🔐 Authenticating...')
    creds = get_credentials()
    drive = build('drive', 'v3', credentials=creds)
    gmail = build('gmail', 'v1', credentials=creds)

    print('\n📤 Uploading images to Google Drive...')
    with open(HTML_FILE, 'r') as f:
        html = f.read()

    for local_path, drive_name in IMAGES:
        cdn_url = upload_image(drive, local_path, drive_name)
        html = html.replace(f'src="{local_path}"', f'src="{cdn_url}"')

    print('\n📧 Creating Gmail draft...')
    msg = MIMEMultipart('alternative')
    msg['Subject'] = SUBJECT
    msg['To']      = TO_EMAIL

    plain = (
        "AgroStar POS — The Operating System. Built to run AgroStar's Retail at scale.\n\n"
        "Live since May 15 · 88 orders · 77 farmers served · ₹1,07,379 GMV · growing every week.\n\n"
        "For feedback: Darpan Pathar, Shantanu Mahajan · Tech & Product, AgroStar"
    )
    msg.attach(MIMEText(plain, 'plain'))
    msg.attach(MIMEText(html, 'html'))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    draft = gmail.users().drafts().create(
        userId='me',
        body={'message': {'raw': raw}}
    ).execute()

    print(f'\n✅ Draft created! ID: {draft["id"]}')
    print(f'   Open Gmail → Drafts to review.')

if __name__ == '__main__':
    main()
