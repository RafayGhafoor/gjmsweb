from concurrent.futures import ThreadPoolExecutor
import requests
import bs4
import concurrent.futures
from time import sleep

ROOT_URL = 'http://gjmsweb.com/'

def fetch_pdf_links(page):
    pdf_links = []
    for i in page.find_all('a'):
        href = i.get('href')
        if href is not None and href.endswith('.pdf'):
            if not href.startswith('http'):
                href = ROOT_URL + href
            pdf_links.append(href)
    return pdf_links

def make_requests(url, timeout):
    return url, requests.get(url, timeout=timeout).status_code

def main():
    response = requests.get('http://gjmsweb.com/archives.php')
    soup = bs4.BeautifulSoup(response.text, 'lxml')
    pdf_links = fetch_pdf_links(soup)
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        future_to_url = {executor.submit(
            make_requests, url, 10): url for url in pdf_links}

        for future in concurrent.futures.as_completed(future_to_url):
            url = future_to_url[future]
            try:
                url, code = future.result()
                if code != 200:
                    print(url)
                    data = future.result()
                print(url)
                sleep(.5)
            except Exception as exc:
                print('%r generated an exception: %s' % (url, exc))
    

if __name__ == "__main__":
    main()
