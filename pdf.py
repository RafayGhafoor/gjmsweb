import re
import os

from tika import tika, parser
from titlecase import titlecase

import util
import pdftitle


class Article:
    '''
        A container for files (articles) which has following attributes.

        Attributes: 
            path = Location of file
            pdf = Parser for file for extracting information
            text = text read from file through parser (pdf)
            filename = Name of file
    '''

    def __init__(self, path):
        self.path = path
        self.pdf = parser.from_file(self.path)

        # Tika returns status code if the file is read properly i.e., 200
        if self.pdf['status'] != 200:
            raise Exception(
                f"Unable to parse pdf file. {self.pdf['status']} returned.")

        self.text = self.pdf['content']
        self.filename = self.pdf['metadata']['resourceName']

    # {found} contains the pattern that are matched to extract information from article
    # which are unable to be retrieved through file's meta_data

    def get_vol_issue(self):
        '''
            Fetch Volume and Issue from file.

            @returns:
                vol_issue (tuple): Volume and Issue in pair. 
                eg: ('4', '2')
        '''
        found = re.search(
            r'Vol\s(?P<volume>(\d)+)\s+[(](\s?)+(?P<issue>(\d)+)', self.text)

        found = re.search(
            r'Vol.?\s+(?P<volume>(\d)+)\s?[,.]?\s+No[.](?P<issue>(\d)+)', self.text) if not found else found

        if found:
            return found.group('volume'), found.group('issue')

    def get_pages(self):
        '''
            Obtain pages range defined on the first page of the article. 

            @returns:
                pages_range (string): Normalized range of pages.
                eg: 12-34
        '''
        found = re.search(
            r'pp(\s?)+[.](\s?)+(?P<paging>(\d\s?)+[-.]+(\s?\d)+)', self.text)

        if found:
            return util.sanitize_page(found.group("paging").replace('\n', ''))

    def get_title(self):
        '''
            Fetch title from the article.

            @returns:
                title (string): Normalized Title extracted from the article. 
        '''
        return titlecase(pdftitle.extract_title(self.path))


class TableGenerator:
    
    ENDPOINTS = ('http://gjmsweb.com/archives/',
                 'http://gjmsweb.com/archives/Current Issue'
                 )

    issue_range = {
        1: "Jan-Mar",
        2: "Apr-Jun",
        3: "Jul-Sept",
        4: "Oct-Dec",
    }


    def __init__(self, meta_data: Article):
        self.title = meta_data.get_title()
        self.fn = meta_data.filename
        self.page_range = meta_data.get_pages()
        self.volume, self.issue = meta_data.get_vol_issue()


    def generate_row(self, number, year=None, current_issue=False):
        '''
            Generate (html) row of table.
            
            @args:
                number: Number of article
            
            @location:
        '''
        if not year:
            import datetime
            year = datetime.datetime.today().year
        
        
        if current_issue:
            link = f'"http://gjmsweb.com/archives/Current Issue/{self.fn}"'
        
        else:
            link = f'"http://gjmsweb.com/archives/Volume {self.volume}/Issue {self.issue.zfill(2)}, {year}/{self.fn}"'
            
            
        return f'''
        <tr>
        <td>{number}</td>
        <td>{self.title}</td>
        <td>{self.page_range}</td>
        <td><a href={link}>PDF</a></td>
        </tr>
        '''


if __name__ == '__main__':
    util.config_tika()
    files = ["./test_files/" +
             i for i in os.listdir('./test_files') if i.endswith(".pdf")]

    for num, i in enumerate(files):
        myarticle = Article(i)
        vol, issue = myarticle.get_vol_issue()
        print(
            f"Name: {myarticle.filename}\nTitle: {myarticle.get_title()}\nPaging: {myarticle.get_pages()}\nVolume: {vol}\nIssue: {issue}\n\n")

    meta_data = {"paging": ""}

    count = 0

    for num, name in enumerate(files, 1):
        article = Article(name)
        if not article.get_vol_issue():
            count += 1
            print(name)
            # print(f"FileName: {name}\nPaging: {info.get_pages()}\nVol: {info.get_vol_issue()}\n")
