import requests
from bs4 import BeautifulSoup
from datetime import datetime
import csv
import argparse
import sys
import time

def check_whatsapp_invite(invite_url):
    """
    Extracts basic public metadata from a WhatsApp group invite link.
    """
    result = {
        "Invite Link": invite_url,
        "Group Name": "-",
        "Status": "Unknown",
        "Members": "-",        # Cannot be extracted reliably via plain HTTP
        "Country": "-",        # Cannot be extracted
        "Admin": "Not Available", # Cannot be extracted
        "Processing Status": "Failed",
        "Timestamp of Scan": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Notes": "Failed"
    }
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36'
    }
    
    try:
        response = requests.get(invite_url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            result["Processing Status"] = "Success"
            soup = BeautifulSoup(response.text, 'html.parser')
            og_title = soup.find("meta", property="og:title")
            
            if og_title and og_title.get("content"):
                result["Group Name"] = og_title.get("content").strip()
                result["Status"] = "Active"
                result["Notes"] = "Valid link"
            else:
                result["Status"] = "Expired"
                result["Notes"] = "Invalid Link"
        else:
            result["Processing Status"] = "Failed"
            result["Notes"] = f"Failed (HTTP {response.status_code})"
            
    except requests.exceptions.RequestException as e:
        result["Processing Status"] = "Failed"
        result["Notes"] = f"Failed ({type(e).__name__})"
        
    return result

def process_csv(input_csv, output_csv):
    # Determine the column headers exactly as requested
    fieldnames = ['Invite Link', 'Group Name', 'Status', 'Members', 'Country', 'Admin', 'Processing Status', 'Timestamp of Scan', 'Notes']
    
    links_to_process = []
    
    # Read input CSV
    try:
        with open(input_csv, mode='r', encoding='utf-8') as infile:
            reader = csv.reader(infile)
            for row in reader:
                if row:  # skip empty lines
                    # Assume link is in the first column, or find it by parsing
                    link = row[0].strip()
                    if link.startswith("http"):
                        links_to_process.append(link)
    except Exception as e:
        print(f"Error reading {input_csv}: {e}")
        sys.exit(1)

    if not links_to_process:
        print(f"No valid links found in {input_csv}.")
        sys.exit(1)

    print(f"Found {len(links_to_process)} links. Processing...")

    # Write output CSV
    try:
        with open(output_csv, mode='w', newline='', encoding='utf-8') as outfile:
            writer = csv.DictWriter(outfile, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
            writer.writeheader()
            
            for index, link in enumerate(links_to_process, start=1):
                print(f"[{index}/{len(links_to_process)}] Checking {link}...")
                data = check_whatsapp_invite(link)
                writer.writerow(data)
                
                # Sleep briefly to be polite and avoid rate limits
                time.sleep(0.5)
                
        print(f"\nDone! Results saved to {output_csv}")
    except Exception as e:
        print(f"Error writing to {output_csv}: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WhatsApp Group Invite Scraper")
    parser.add_argument("--input", "-i", required=True, help="Input CSV file containing links")
    parser.add_argument("--output", "-o", required=True, help="Output CSV file for results")
    
    args = parser.parse_args()
    process_csv(args.input, args.output)
