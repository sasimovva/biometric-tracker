#!/usr/bin/env python3
import os
import sys
import argparse
from playwright.sync_api import sync_playwright

SESSION_FILE = "garmin_session.json"

def login_and_save_session():
    """Opens a headful browser for the user to log in manually, complete 2FA/CAPTCHA,
    and then saves the session state to garmin_session.json.
    """
    print("🔑 Session Setup Mode")
    print("---------------------------------------------------------")
    print("1. A browser window will open in headed mode.")
    print("2. Please log in to your Garmin Connect account.")
    print("3. Complete any Multi-Factor Authentication (2FA) or captcha if prompted.")
    print("4. Once you are successfully logged in and see your dashboard,")
    print("   return here and press Enter to save the session state.")
    print("---------------------------------------------------------\n")
    
    input("Press [Enter] to launch the browser...")
    
    with sync_playwright() as p:
        # Launch headful so the user can interact
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={'width': 1280, 'height': 800},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        
        print("🔗 Navigating to Garmin Connect sign-in page...")
        page.goto("https://connect.garmin.com/signin")
        
        print("\n⏳ Browser is active. Please complete sign-in in the browser window.")
        input("👉 Once signed in and the dashboard has loaded, press [Enter] here to save session: ")
        
        # Save storage state (cookies, local storage, etc.)
        context.storage_state(path=SESSION_FILE)
        print(f"\n💾 Session state saved successfully to '{SESSION_FILE}'!")
        browser.close()

def download_fit_file(activity_id, output_path):
    """Launches a headless browser using the saved session state, navigates to the 
    download URL for the specified activity, and saves the resulting zip/fit file.
    """
    if not os.path.exists(SESSION_FILE):
        print(f"❌ Error: Session file '{SESSION_FILE}' not found.")
        print("👉 Please run the login command first: python3 download_garmin_fit.py --login")
        sys.exit(1)
        
    print(f"🔄 Initializing headless browser using saved session...")
    
    with sync_playwright() as p:
        # Run headless since session is saved
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            storage_state=SESSION_FILE,
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        
        # Target export URL
        download_url = f"https://connect.garmin.com/modern/proxy/download-service/files/activity/{activity_id}"
        print(f"📥 Requesting download: {download_url}")
        
        try:
            # expectation block handles download trigger
            with page.expect_download() as download_info:
                page.goto(download_url)
            
            download = download_info.value
            
            # Resolve default download name if output path is a directory
            if os.path.isdir(output_path) or output_path.endswith('/') or output_path == "":
                dest_dir = output_path if output_path != "" else "."
                dest_file = os.path.join(dest_dir, download.suggested_filename)
            else:
                dest_file = output_path
                
            download.save_as(dest_file)
            print(f"✅ Download completed successfully!")
            print(f"💾 File saved to: {os.path.abspath(dest_file)}")
            
        except Exception as e:
            print(f"❌ Failed to download activity {activity_id}: {e}")
            print("💡 Your session may have expired. Try running the login command again to refresh cookies.")
            
        browser.close()

def main():
    parser = argparse.ArgumentParser(description="Headless Playwright Downloader for Garmin Connect FIT files.")
    
    # Define argument group
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--login', action='store_true', help="Open headed browser to log in and save session state.")
    group.add_argument('--activity', type=str, help="Activity ID to download original FIT/ZIP file.")
    
    parser.add_argument('--output', type=str, default="", help="Destination path/filename for the download. Defaults to suggested filename in current directory.")
    
    args = parser.parse_args()
    
    if args.login:
        login_and_save_session()
    elif args.activity:
        # Default to current directory if not specified
        output = args.output if args.output else "."
        download_fit_file(args.activity, output)

if __name__ == "__main__":
    main()
