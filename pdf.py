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
    
    def get_authors(self):
        word = "abstract"
        splitter = (word.upper(), word.capitalize())
        content_till_abstract = [self.text.split(abstract)[0].strip() for abstract in splitter if abstract in self.text][0]
        if not content_till_abstract:
            raise Exception(f"Author not found for: {self.filename}.")
        return [i.strip()[:-1] for i in content_till_abstract.split('\n')[-1].split(',')]
        

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

    def get_published_year(self, volume_number, start_year=2015):
        '''Obtain published year of the article.'''
        return start_year + (int(volume_number) - 1)

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


class TableHandler:

    ISSUE_RANGE = {
        1: "Jan-Mar",
        2: "Apr-Jun",
        3: "Jul-Sept",
        4: "Oct-Dec",
    }

    def __init__(self, meta_data: Article, endpoint='archives.php'):
        self.title = meta_data.get_title()
        self.filename = meta_data.filename
        self.page_range = meta_data.get_pages()
        self.volume, self.issue = meta_data.get_vol_issue()
        self.year = meta_data.get_published_year(self.volume)
        self.authors = meta_data.get_authors()
        self.endpoint = open(endpoint, 'r+')

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        self.endpoint.close()

    def generate_header(self):
        '''Produces header for the table.'''
        return f"Vol {self.volume} - No. {self.issue} ({' - '.join(self.ISSUE_RANGE[int(self.issue)].split('-'))}, {self.year})"

    def generate_row(self, number, year=None, current_issue=False):
        '''
            Generate (html) row of table.

            @args:
                number: Number of article

            @location:
        '''
        if current_issue:
            link = f'"http://gjmsweb.com/archives/Current Issue/{self.filename}"'

        else:
            link = f'"http://gjmsweb.com/archives/{self.year}/Volume {self.volume}/Issue {self.issue}, {self.year}/{self.filename}"'

        return f'''
        <tr>
        <td>{number}</td>
        <td>{self.title} - {self.filter_author()[0]}</td>
        <td>{self.page_range}</td>
        <td><a href={link}>PDF</a></td>
        </tr>
        '''
        
    def filter_author(self, key="Abdul Ghafoor"):
        return [author for author in self.authors if key not in author]
        

    def add_section(self):
        blockquote = self.generate_header()
        soup_obj = util.create_soup(self.endpoint)
        last_table = util.get_last_volume(soup_obj)
        last_table.append(util.get_template(blockquote))
        print(soup_obj.prettify(formatter=None))
        # self.endpoint.write(soup_obj)

    def add_article(self):
        # Raise exception when the section doesn't exist
        pass


if __name__ == '__main__':
    util.config_tika()
    files = ["./test_files/" +
             i for i in os.listdir('./test_files') if i.endswith(".pdf")]

    articles = []



    for i in files:
        articles.append(TableHandler(Article(i)))

    issue_cache = 0
    index = 1

    for i in sorted(articles, key=util.fetch_articles_sorting_key): 
        if not issue_cache:
            issue_cache = i.issue

        elif issue_cache != i.issue:
            issue_cache = i.issue
            index = 1

        print(i.generate_row(index))
        index += 1


    # SEPARATE
        # generator = TableHandler(i)
        # print(generator.generate_block_quote())
        # print(generator.generate_row(num+1))

        # vol, issue = myarticle.get_vol_issue()
        # print(f"Name: {myarticle.filename}\nTitle: {myarticle.get_title()}\nPaging: {myarticle.get_pages()}\nVolume: {vol}\nIssue: {issue}\n\n")
