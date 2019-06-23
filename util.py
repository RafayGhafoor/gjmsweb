import os
from tika import tika


def config_tika(tika_jar_path=__file__):
    '''Configure Tika variables to support tika-server.jar file from provided path i.e., tika_jar_path.'''
    # Store the absolute path of your file (containing .jar)

    abs_path = os.path.dirname(os.path.join(os.getcwd(), tika_jar_path))
    # Update the required variables

    tika.log_path = os.getenv('TIKA_LOG_PATH', abs_path)
    tika.TikaJarPath = os.getenv('TIKA_PATH', abs_path)
    tika.TikaFilesPath = abs_path
    TikaServerLogFilePath = tika.log_path


#TODO: Better name for function
def fetch_articles_sorting_key(article: list):
        '''
        Provides a base key for sorting the articles according to the ascending order of their
        page range.
        
        Since every article has a page range
        for eg: 13-33
        
        @args:
            article (TableGenerator): List of TableGenerator objects which are to be sorted
        
        @returns (int):
            returns 13
        
        Example:
        
            To be used as a key for sorted function.
            ```
                sorted(articles, key=util.fetch_articles_sorting_key)
            ```
        '''
        return int(article.page_range.split('-')[0])


def sanitize_page(page):
    '''Sanitizes page range removing redundant period symbols and replacing it with dash.'''
    if not '.' in page and '-' in page:
        return page

    elif '.' in page and '-' not in page:
        return page.replace('.', '-')
    
    elif '.' in page:
        return page.replace('.', '')


    return page


if __name__ == "__main__":
    a = ('1-13', '1-.13','1.13')
    for i in map(sanitize_page, a):
        print(i)