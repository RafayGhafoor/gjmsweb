# pip install "paddleocr>=2.0.1" PyMuPDF
import fitz
import re

class AcademicPDFTitleExtractor:
    def __init__(self):
        self.common_headers = ['abstract', 'introduction', 'research article', 'original article']
    
    def extract_title_from_text(self, text):
        """Extract title using text pattern matching for academic papers"""
        if not text:
            return None
            
        # Split into lines and clean
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        
        if not lines:
            return None
            
        # Skip journal header/metadata lines
        start_idx = 0
        for idx, line in enumerate(lines):
            if any(word in line.lower() for word in ['journal', 'vol', 'issn', 'doi']):
                start_idx = idx + 1
            else:
                break
                
        # Look for title after metadata
        candidate_lines = []
        for line in lines[start_idx:]:
            # Skip if line looks like a header
            if line.lower() in self.common_headers:
                continue
                
            # Skip if line contains typical metadata patterns
            if re.search(r'\d{4}|vol\.|issn|doi|©|copyright', line.lower()):
                continue
                
            # Add to candidates if line looks like a title
            if len(line) > 15 and line.istitle() or line.isupper():
                candidate_lines.append(line)
            
            # Stop when we hit abstract or other section markers
            if any(header in line.lower() for header in self.common_headers):
                break
                
        if candidate_lines:
            # Join multi-line titles
            title = ' '.join(candidate_lines)
            # Clean up
            title = re.sub(r'\s+', ' ', title).strip()
            return title
            
        return None

    def extract_from_pdf(self, pdf_path):
        """Main method to extract title from PDF"""
        try:
            # Open PDF and get first page text
            doc = fitz.open(pdf_path)
            first_page = doc[0]
            
            # Get text from top portion of first page
            page_height = first_page.rect.height
            top_text = first_page.get_text(
                "text",
                clip=(0, 0, first_page.rect.width, page_height * 0.3)
            )
            doc.close()
            
            # Extract title
            title = self.extract_title_from_text(top_text)
            return title
            
        except Exception as e:
            print(f"Error extracting from PDF: {e}")
            return None

# Usage example
if __name__ == "__main__":
    extractor = AcademicPDFTitleExtractor()
    pdf_path = "Fatima Article.pdf"
    title = extractor.extract_from_pdf(pdf_path)
    print(f"Extracted title: {title}")