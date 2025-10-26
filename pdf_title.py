import fitz  # PyMuPDF


def extract_first_page_text(pdf_path: str) -> str:
    """
    Extract text from the first page of a PDF file.

    Args:
        pdf_path (str): Path to the PDF file

    Returns:
        str: Extracted text from the first page
    """
    try:
        # Open the PDF file
        doc = fitz.open(pdf_path)

        # Get the first page
        page = doc[0]

        # Extract text from the page
        text = page.get_text()

        # Close the document
        doc.close()

        return text

    except Exception as e:
        return f"Error: {str(e)}"


# Example usage
if __name__ == "__main__":

    pdf_path = "example.pdf"  # Replace with your PDF path
    text = extract_first_page_text(pdf_path)
    print(text)
