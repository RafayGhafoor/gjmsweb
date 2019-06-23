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


def sanitize_page(page):
    '''Sanitizes page range removing redundant period symbols and replacing it with dash.'''
    if not '.' in page:
        return page

    elif '-' in page and '.' in page:
        return page.replace('.', '')

    elif '.' in page and '-' not in page:
        return page.replace('.', '-')

    return page
