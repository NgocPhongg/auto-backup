"""
get_new_token.py — Lay Refresh Token MOI cho Hotmail/Outlook.
===================================================================
Su dung Device Code Flow — khong can redirect URI.
Chi can mo link va nhap ma tren trinh duyet.

Cach dung:
    python get_new_token.py
"""

import msal
import sys
import time

# === CAU HINH ===
CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"
AUTHORITY = "https://login.microsoftonline.com/consumers"

# Cac scope se thu lan luot
SCOPES_LIST = [
    ["https://outlook.office.com/IMAP.AccessAsUser.All"],
    ["https://outlook.office365.com/IMAP.AccessAsUser.All"],
    ["https://graph.microsoft.com/IMAP.AccessAsUser.All"],
    ["https://graph.microsoft.com/Mail.ReadWrite"],
]


def main():
    print("=" * 60)
    print("  LAY REFRESH TOKEN MOI CHO HOTMAIL/OUTLOOK")
    print("  (Device Code Flow — khong can redirect URI)")
    print("=" * 60)
    print()
    print(f"  Client ID: {CLIENT_ID}")
    print()
    
    app = msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY)
    
    # Thu tung scope
    for scopes in SCOPES_LIST:
        scope_name = scopes[0].split("/")[-1]
        resource = scopes[0].split("/")[2]
        print(f"--- Thu scope: {resource}/{scope_name} ---")
        
        try:
            flow = app.initiate_device_flow(scopes=scopes)
        except Exception as e:
            print(f"  Khong tao duoc device flow: {e}")
            print()
            continue
        
        if "user_code" not in flow:
            err = flow.get("error_description", flow.get("error", "Unknown"))
            print(f"  LOI: {err[:150]}")
            print()
            continue
        
        # Hien thi huong dan
        print()
        print("  " + "=" * 50)
        print(f"  MO TRINH DUYET VA VAO TRANG:")
        print(f"  >>> {flow['verification_uri']} <<<")
        print()
        print(f"  NHAP MA NAY:")
        print(f"  >>> {flow['user_code']} <<<")
        print("  " + "=" * 50)
        print()
        print(f"  Sau khi nhap ma, dang nhap tai khoan Hotmail/Outlook")
        print(f"  roi quay lai day. Dang cho...")
        print()
        
        # Cho user dang nhap (polling)
        result = app.acquire_token_by_device_flow(flow)
        
        if "access_token" in result:
            access_token = result['access_token']
            new_rt = result.get('refresh_token', '')
            
            print("=" * 60)
            print("  THANH CONG!")
            print("=" * 60)
            print()
            
            if new_rt:
                print("*** REFRESH TOKEN MOI (copy toan bo dong duoi) ***")
                print()
                print(new_rt)
                print()
                print("=" * 60)
                print(f"Client ID: {CLIENT_ID}")
                print(f"Scope thanh cong: {scopes[0]}")
                print()
                print("HUONG DAN:")
                print("1. Copy Refresh Token o tren")
                print("2. Paste vao cot 'Refresh Token' trong bang Dang Ky")
                print(f"3. Cot 'Client ID' dien: {CLIENT_ID}")
                print()
                
                # Test IMAP
                print("--- Test IMAP ---")
                test_email = input("Nhap email de test IMAP (Enter = bo qua): ").strip()
                if test_email:
                    try:
                        from imap_tools import MailBox, AND
                        mailbox = MailBox('imap-mail.outlook.com')
                        mailbox.xoauth2(test_email, access_token)
                        print(">>> IMAP LOGIN THANH CONG! <<<")
                        count = 0
                        for msg in mailbox.fetch(AND(seen=False), reverse=True, limit=3):
                            count += 1
                            sender = msg.from_ or 'N/A'
                            subj = (msg.subject or 'N/A')[:50]
                            print(f"  Mail {count}: {sender} - {subj}")
                        if count == 0:
                            print("  (Khong co mail chua doc)")
                        mailbox.logout()
                    except Exception as e:
                        print(f"  IMAP LOI: {e}")
            else:
                print("CANH BAO: Khong nhan duoc Refresh Token!")
                print("App co the chua co quyen offline_access.")
            
            return  # Xong!
        else:
            error = result.get('error', '')
            desc = result.get('error_description', '')[:200]
            print(f"  LOI: {error}")
            print(f"  {desc}")
            print()
    
    print()
    print("THAT BAI: Khong the lay token voi bat ky scope nao.")
    print("Kiem tra lai Client ID hoac lien he nguoi ban tai khoan.")


if __name__ == '__main__':
    main()
