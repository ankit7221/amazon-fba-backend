import os
import hashlib
import hmac
import base64
import datetime
import json
import requests
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv

# Load environment variables from .env file (for local development)
load_dotenv()

app = Flask(__name__)
CORS(app)

# --- Configuration (Load from environment variables) ---
ASSOCIATE_TAG = os.getenv('ASSOCIATE_TAG')
ACCESS_KEY_ID = os.getenv('ACCESS_KEY_ID')
SECRET_ACCESS_KEY = os.getenv('SECRET_ACCESS_KEY')
HOST = os.getenv('HOST', "webservices.amazon.in")
REGION = os.getenv('REGION', "eu-west-1")
SERVICE = "ProductAdvertisingAPI"

# --- TEMPORARY DIAGNOSTIC PRINTS (for local debugging) ---
print("--- Environment Variable Check ---")
print(f"ASSOCIATE_TAG: {ASSOCIATE_TAG}")
# Print partial keys for security, or 'None' if not set
print(f"ACCESS_KEY_ID: {ACCESS_KEY_ID[:5]}...{ACCESS_KEY_ID[-5:]}" if ACCESS_KEY_ID else "None")
print(f"SECRET_ACCESS_KEY: {'***** (masked)'}" if SECRET_ACCESS_KEY else "None")
print(f"HOST: {HOST}")
print(f"REGION: {REGION}")
print("----------------------------------")
# --- END TEMPORARY DIAGNOSTIC PRINTS ---


# --- Basic check for credentials at startup ---
# --- Basic check for credentials at startup ---
if not all([ASSOCIATE_TAG, ACCESS_KEY_ID, SECRET_ACCESS_KEY]):
    print("\n" + "="*80)
    print("CRITICAL ERROR: Missing Amazon API credentials in environment variables.")
    print("Please set ASSOCIATE_TAG, ACCESS_KEY_ID, and SECRET_ACCESS_KEY in your .env file (locally)")
    print("or in your hosting provider's environment variables (for deployment).")
    print("The application will likely fail to make Amazon API calls without these.")
    print("="*80 + "\n")
    # exit("Cannot start backend without API credentials.") # TEMPORARILY COMMENTED OUT


# --- PA-API 5.0 Request Signing Function ---
def sign_paapi_request(access_key, secret_key, host, region, service, api_path, payload):
    t = datetime.datetime.utcnow()
    amz_date = t.strftime('%Y%m%dT%H%M%SZ')
    date_stamp = t.strftime('%Y%m%d')

    canonical_uri = api_path
    canonical_querystring = ''
    canonical_headers = 'host:' + host + '\n' + 'x-amz-date:' + amz_date + '\n'
    signed_headers = 'host;x-amz-date'

    payload_str = json.dumps(payload, separators=(',', ':'))
    payload_hash = hashlib.sha256(payload_str.encode('utf-8')).hexdigest()

    canonical_request = '\n'.join([
        'POST',
        canonical_uri,
        canonical_querystring,
        canonical_headers,
        signed_headers,
        payload_hash
    ])

    algorithm = 'AWS4-HMAC-SHA256'
    credential_scope = '/'.join([date_stamp, region, service, 'aws4_request'])
    string_to_sign = '\n'.join([
        algorithm,
        amz_date,
        credential_scope,
        hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()
    ])

    def sign_key(key, msg):
        return hmac.new(key, msg.encode('utf-8'), hashlib.sha256).digest()

    k_date = sign_key(('AWS4' + secret_key).encode('utf-8'), date_stamp)
    k_region = sign_key(k_date, region)
    k_service = sign_key(k_region, service)
    k_signing = sign_key(k_service, 'aws4_request')

    signature = hmac.new(k_signing, string_to_sign.encode('utf-8'), hashlib.sha256).hexdigest()

    authorization_header = (
        algorithm + ' ' +
        'Credential=' + access_key + '/' + credential_scope + ', ' +
        'SignedHeaders=' + signed_headers + ', ' +
        'Signature=' + signature
    )

    headers = {
        'Content-Type': 'application/json; charset=utf-8',
        'X-Amz-Date': amz_date,
        'X-Amz-Target': f'{service}.SearchItems',
        'Authorization': authorization_header,
        'Host': host
    }
    return headers

# --- Flask Routes ---

@app.route('/api/search', methods=['POST'])
def search_products():
    data = request.get_json()
    keywords = data.get('keywords')
    prime_only = data.get('primeOnly', True)

    if not keywords:
        return jsonify({"error": "Keywords are required."}), 400

    api_path = "/paapi5/searchitems"
    api_endpoint = f"https://{HOST}{api_path}"

    payload = {
        "Keywords": keywords,
        "SearchIndex": "All",
        "PartnerTag": ASSOCIATE_TAG,
        "PartnerType": "Associates",
        "ItemCount": 10,
        "Resources": [
            "Images.Primary.Medium",
            "ItemInfo.Title",
            "Offers.Listings.Price",
            "Offers.Listings.IsPrimeEligible",
        ]
    }

    try:
        headers = sign_paapi_request(ACCESS_KEY_ID, SECRET_ACCESS_KEY, HOST, REGION, SERVICE, api_path, payload)
        
        response = requests.post(api_endpoint, headers=headers, data=json.dumps(payload))
        response.raise_for_status()
        api_response = response.json()

        filtered_items = []
        if 'SearchResult' in api_response and 'Items' in api_response['SearchResult']:
            for item in api_response['SearchResult']['Items']:
                is_prime_eligible = False
                if 'Offers' in item and 'Listings' in item['Offers'] and item['Offers']['Listings']:
                    first_listing = item['Offers']['Listings'][0]
                    if first_listing.get('IsPrimeEligible', False):
                        is_prime_eligible = True
                
                if prime_only and not is_prime_eligible:
                    continue

                filtered_items.append(item)
        
        return jsonify({"Items": filtered_items, "TotalFilteredCount": len(filtered_items)})

    except requests.exceptions.RequestException as e:
        print(f"API Request Error: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"Amazon API Raw Response (error): {e.response.text}")
            try:
                error_details = e.response.json()
                if 'Errors' in error_details:
                    return jsonify({"error": f"Amazon API Error: {error_details['Errors'][0]['Message']}"}), 500
            except json.JSONDecodeError:
                pass
        return jsonify({"error": f"Failed to connect to Amazon API or unhandled API error: {e}"}), 500
    except json.JSONDecodeError:
        print(f"JSON Decode Error: Response was not valid JSON. Raw response: {response.text if 'response' in locals() else 'No response object available'}")
        return jsonify({"error": "Invalid JSON response from Amazon API. Please check backend logs."}), 500
    except Exception as e:
        print(f"An unexpected server error occurred: {e}")
        return jsonify({"error": f"An unexpected server error occurred: {e}"}), 500

@app.route('/')
def serve_index():
    return send_from_directory('../frontend', 'index.html')

@app.route('/<path:path>')
def serve_static_files(path):
    return send_from_directory('../frontend', path)


if __name__ == '__main__':
    app.run(debug=True, port=5000)