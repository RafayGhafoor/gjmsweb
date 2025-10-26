import requests
import os
from datetime import datetime
from pathlib import Path
import mimetypes
import hashlib
import hmac
import base64
from dotenv import load_dotenv

load_dotenv()


class BunnyUploader:
    def __init__(self, storage_zone_name=None, api_key=None, storage_zone_region="sg"):
        """
        Initialize BunnyUploader with credentials
        
        Args:
            storage_zone_name (str): Name of your storage zone
            api_key (str): Your API access key
            storage_zone_region (str): Region code (default: "de" for Germany)
        """
        self.storage_zone = storage_zone_name if storage_zone_name else os.getenv("STORAGE_ZONE")
        self.api_key = api_key if api_key else os.getenv("API_KEY")
        self.region = storage_zone_region
        self.base_url = f"https://{storage_zone_region}.storage.bunnycdn.com/{storage_zone_name}/"
        self.headers = {
            "AccessKey": api_key,
            "accept": "application/json"
        }

    def calculate_sha256(self, file_path):
        """Calculate SHA256 hash of file"""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()

    def get_checksum_header(self, file_path):
        """Generate checksum header for file upload"""
        file_hash = self.calculate_sha256(file_path)
        message = f"{self.storage_zone}{file_hash}".encode('utf-8')
        signature = base64.b64encode(
            hmac.new(self.api_key.encode('utf-8'), message, hashlib.sha256).digest()
        ).decode('utf-8')
        return signature

    def upload_file(self, file_path, remote_path):
        """
        Upload a single file to Bunny storage
        
        Args:
            file_path (str): Local path to file
            remote_path (str): Destination path on Bunny storage
        """
        url = self.base_url + remote_path
        
        # Get file mime type
        content_type = 'application/octet-stream'
        
        # Add checksum header
        headers = self.headers.copy()

        try:
            with open(file_path, 'rb') as f:
                response = requests.put(url, data=f, headers=headers)
                print(response.text)
                response.raise_for_status()
                return True
        except Exception as e:
            print(f"Error uploading {file_path}: {str(e)}")
            return False

    def upload_directory(self, local_dir, volume, issue):
        """
        Upload all files from a directory to appropriate Bunny storage location
        
        Args:
            local_dir (str): Local directory containing files
            volume (int): Volume number
            issue (int): Issue number
        """
        # Calculate year based on volume (assuming Vol 1 started in 2015)
        year = 2015 + (volume - 1)
        
        # Create remote directory path
        remote_dir = f"{year}/Volume {volume}/Issue {issue}, {year}/"
        
        success_count = 0
        error_count = 0
        
        for file_name in os.listdir(local_dir):
            if file_name.lower().endswith('.pdf'):
                local_path = os.path.join(local_dir, file_name)
                remote_path = remote_dir + file_name
                
                print(f"Uploading {file_name} to {remote_path}...")
                
                if self.upload_file(local_path, remote_path):
                    success_count += 1
                else:
                    error_count += 1
        
        return success_count, error_count

# def main():
#     # Get credentials from environment variables for security
    
    
#     if not STORAGE_ZONE or not API_KEY:
#         raise ValueError("Please set BUNNY_STORAGE_ZONE and BUNNY_API_KEY environment variables")

#     # Initialize uploader
#     uploader = BunnyUploader(STORAGE_ZONE, API_KEY)
    
#     # Directory containing PDFs to upload
#     test_files_dir = "test_files"
    
#     # Volume and issue information (can be passed as arguments)
#     volume = 10
#     issue = 4
    
#     # Upload files
#     success, errors = uploader.upload_directory(test_files_dir, volume, issue)
    
#     print(f"\nUpload complete!")
#     print(f"Successfully uploaded: {success} files")
#     print(f"Failed uploads: {errors} files")

# if __name__ == "__main__":
#     main()