#!/usr/bin/env python
# -*- coding: utf-8 -*-

import argparse
import copy
import os
import re
import subprocess
import sys
from io import StringIO

import xml.etree.ElementTree as etree
from bs4 import BeautifulSoup


def parse_xml(s):
    return etree.parse(s)


VERSION = '1.3'


class CONFIG:

    def __init__(self, filename, rename=False, multiline=True, top_margin=70, min_length=15, max_length=250, debug=False):
        self.filename = filename
        self.rename = rename
        self.multiline = multiline
        self.top_margin = top_margin
        self.min_length = min_length
        self.max_length = max_length
        self.debug = debug

def replaceAll(text, lst=('<html>', '</html>', '<body>', '</body>')):
    for i in lst:
        text = text.replace(i, '')
    return text


def convert_pdf_to_xml(path):
    """Return XML string of converted PDF file."""
    cmd = ['pdftohtml', '-xml', '-f', '1', '-l', '1',
           '-i', '-q', '-nodrm', '-hidden', '-stdout', path]
    # https://stackoverflow.com/questions/15374211/why-does-popen-communicate-return-bhi-n-instead-of-hi
    xml_string = subprocess.check_output(
        cmd, stderr=open(os.devnull, 'w'), universal_newlines=True)
    soup = BeautifulSoup(xml_string, 'xml')
    text = replaceAll(str(soup))
    return parse_xml(StringIO(remove_control_chars(text)))

    # return parse_xml(StringIO(xml_string))
    # return parse_xml(StringIO(remove_control_chars(xml_string)))


def remove_control_chars(string):
    """Filter ASCII control characters as etree treats them as invalid."""
    return ''.join([i for i in string if ord(i) in [9, 10, 13] or ord(i) >= 32])


def font_specs(xml_data):
    """Return all font specifications in XML."""
    xml_font_specs=xml_data.findall('page[@number="1"]/fontspec[@id][@size]')
    return [fs.attrib for fs in xml_font_specs]


def sorted_font_ids(font_specs):
    """Return sorted font specifications by size decending."""
    font_specs=sorted(font_specs, key=lambda x: int(x['size']), reverse=True)
    return [fs['id'] for fs in font_specs]


def textblocks_by_id(xml_data, font_id):
    """Return text blocks given font id."""
    text_elements=xml_data.findall(
        'page[@number="1"]/text[@font="%s"]' % font_id)
    first_page_top=int(xml_data.findall('page[@number="1"]')[0].get('top'))
    first_page_height=int(xml_data.findall(
        'page[@number="1"]')[0].get('height'))
    return top_and_texts(text_elements, first_page_top, first_page_height)


def top_and_texts(text_elements, page_top, page_height):
    """Return top position of first non-empty text line and all
    unformatted non-empty text lines, and some extra (page) metadata.
    Example: {
      'pageTop': 0,
      'pageHeight': 1263,
      'blockTop': 16,
      'blockText': [
        {'top': 16, 'height': 24, 'text': 'foo'},
        {'top': 30, 'height': 24, 'text': 'bar'},
        {'top': 44, 'height': 24, 'text': 'baz'}
      ]
    }"""
    text_lines=[]
    top=page_top

    for text_element in text_elements:
        text_line=unformat_and_strip(text_element)
        if not text_line:
            continue
        t=int(text_element.get('top'))
        h=int(text_element.get('height'))
        w=int(text_element.get('width'))
        # TODO: Maybe allow a light error here
        # if T < Top - Error:
        # TODO: This is actually a filter
        if t < top:
            # Ignore text lines positioned upwards. Only look downwards.
            continue
        top=t
        text_lines.append({
            'top': t,
            'height': h,
            'width': w,
            'text': text_line
        })

    if text_lines and top > page_top:
        return {
            'pageTop': page_top,
            'pageHeight': page_height,
            'blockTop': min(text_lines, key=lambda x: x['top'])['top'],
            'blockText': text_lines
        }
    else:
        return {}


def filter_empties(text_blocks, _config):
    """Filter emtpy text blocks."""
    return [tb for tb in text_blocks if tb and tb['blockText']]


def unformat_and_strip(text_element):
    """Return non-empty unformatted text element."""
    return ''.join(text_element.itertext()).strip()


def filter_bottom_half(text_blocks, _config):
    """Filter text blocks on lower half of page."""
    return [tb for tb in text_blocks if
            tb['blockTop'] - tb['pageTop'] < tb['pageHeight'] / 2]


def filter_margin(text_blocks, config):
    """Filter text blocks above certain top margin."""
    return [tb for tb in text_blocks if tb['blockTop'] > config.top_margin]


def filter_vertical(text_blocks, _config):
    """Filter text blocks with vertical text."""
    new_text_blocks=[]
    for tb in text_blocks:
        new_tb=copy.copy(tb)
        new_tb['blockText']=[]
        for t in tb['blockText']:
            if t['width'] > 0:
                new_tb['blockText'].append(t)
        if new_tb['blockText']:
            new_text_blocks.append(new_tb)
    return new_text_blocks


def filter_shorts(text_blocks, config):
    """Filter text lines which are too short thus unlikely titles."""
    return [tb for tb in text_blocks if
            len(' '.join([t['text'] for t in tb['blockText']])) >= config.min_length]


def filter_longs(text_blocks, config):
    """Filter text lines which are too long thus unlikely titles."""
    return [tb for tb in text_blocks if
            len(' '.join([t['text'] for t in tb['blockText']])) <= config.max_length]


def filter_unrelated_lines(text_blocks, _config):
    """Filter text lines in text blocks that are too far away from previous
    lines."""
    new_text_blocks=[]
    for tb in text_blocks:
        new_tb=copy.copy(tb)
        new_tb['blockText']=[]
        next_top=tb['blockTop']
        for t in tb['blockText']:
            if t['top'] < next_top + t['height'] / 2:
                next_top=t['top'] + t['height']
                new_tb['blockText'].append(t)
        if new_tb['blockText']:
            new_text_blocks.append(new_tb)
    return new_text_blocks

def my_filter(text):
    keywords = ['Open Access', "Global Journal", '____', 'figshare']
    for i in keywords:
        if i in text:
            return True

def choose_title(text_blocks, config):
    """Return title as UTF-8 from list. Either all non-empty texts with font id
    or just first."""
    # Have to encode output when piping script. See: http://goo.gl/h0ql0
    for tb in text_blocks:
        text = ' '.join([t['text'] for t in tb['blockText']]).encode('utf-8').decode()
        if my_filter(text) or ' ' not in text: continue
        if config.multiline:
            # print(' '.join([t['text'] for t in tb['blockText']]).encode('utf-8'))
            return ' '.join([t['text'] for t in tb['blockText']]).encode('utf-8')
        else:
            return tb['blockText'][0]['text'].encode('utf-8')
    return None


def format_upper_case(title, _config):
    """Return the title in titlecase if all letters are uppercase."""
    return title.title() if is_mostly_upper_case(title) else title


def is_mostly_upper_case(string, threshold=0.67):
    """Return True if string has over Threshold uppercase letters, else False."""
    n=0
    for c in string:
        if c.isupper() or c.isspace():
            n=n+1
    if float(n) / len(string) >= threshold:
        return True
    else:
        False


def format_weird_case(title, _config):
    """Return the title in titlecase if all letters are uppercase."""
    return title.title() if is_weird_case(title) else title


def is_weird_case(string):
    """Return True if given String has "weird" cases in case letters, else False.
    Example: isWeirdCase('A FAult-tolerAnt token BAsed Algorithm') == True"""
    for i in range(len(string) - 2):
        if string[i].isalpha() and (
           string[i+1].isupper() and string[i+2].islower() or
           string[i+1].islower() and string[i+2].isupper()):
            return True
    return False


def format_space_case(title, _config):
    """Return the title removing gaps between letters."""
    if is_space_case(title):
        return unspace(title)
    else:
        return title


def is_space_case(string, threshold=0.2):
    """Return True if given String has many gaps between letters, else False.
    Example: isSpaceCase('A H i gh - L e ve l F r am e w or k f or') == True"""
    n=0
    for c in string:
        if c.isspace():
            n=n+1
    if float(n) / len(string) >= threshold:
        return True
    else:
        False


def unspace(string):
    """Return the given string without the many gaps between letters.
    Example: unspace('A H i gh - L e ve l F r am e') == A High-Level Frame"""
    joined_string=''.join(string.split())
    return re.sub(r'([^-])([A-Z])', r'\1 \2', joined_string)


def format_multi_spaces(title, _config):
    """Return the title with not more than one space per word separation."""
    # TODO: These are actually two formatters in one
    return ' '.join(title.split()).replace(' :', ':')


def format_linebreak_dash(title, _config):
    """Return the title without linebreak dash."""
    return re.sub(r'(\S)- (.+)', r'\1-\2', title)


def format_trailing_period(title, _config):
    """Return the title without trailing period."""
    return re.sub(r'^(.*)\.$', r'\1', title)


def format_trailing_asterik(title, _config):
    """Return the title without trailing asterik."""
    return re.sub(r'^(.*)\*$', r'\1', title)


def format_quotes(title, _config):
    """Return the title with normalized quotes."""
    return title.replace('‘‘', '“') \
                .replace('’’', '”') \
                .replace('``', '‟') \
                .replace(',,', '„')


# TODO: Generalize functionality to convert Unicode NFD->NFC.
def format_ligatures(title, _config):
    """Return the title without Ligatures."""
    # For a reference of the list see: http://typophile.com/files/PMEJLigR_6061.GIF
    # and https://github.com/Docear/PDF-Inspector/blob/master/src/org/docear/pdf/util/ReplaceLigaturesFilter.java
    title=str(title.decode('utf-8'))
    return title.replace('ﬁ', 'fi') \
                .replace('ﬂ', 'fl')


def transduce(funs, value, config):
    """Return a value after applying a list of functions until list or value is
    empty."""
    if not (funs and value):
        return value
    return transduce(funs[1:], funs[0](value, config), config)


def extract_title(path):
    """Return title in PDF article after applying rules and filters."""
    config=CONFIG(filename=path)

    groupers=[
    ]

    filters=[
        filter_empties,
        filter_bottom_half,
        filter_margin,
        filter_vertical,
        filter_shorts,
        filter_longs,
        filter_unrelated_lines,
        choose_title
    ]

    formatters=[
        format_ligatures,
        format_upper_case,
        format_weird_case,
        format_space_case,
        format_multi_spaces,
        format_linebreak_dash,
        format_trailing_period,
        format_trailing_asterik,
        format_quotes
    ]

    xml_data=convert_pdf_to_xml(path)
    font_ids=sorted_font_ids(font_specs(xml_data))
    text_blocks=[textblocks_by_id(xml_data, font_id) for font_id in font_ids]
    return transduce(groupers + filters + formatters, text_blocks, config)


def sanitize_filename(filename):
    return filename.replace(':', ' -').replace('/', '-')


def pos_int(v):
    i=int(v)
    if i > 0:
        return i
    raise argparse.ArgumentTypeError("invalid pos_int value: " % v)


def filepath(v):
    f=os.path.expanduser(v.strip())
    if not os.path.isfile(f) and not os.path.islink(f):
        raise argparse.ArgumentTypeError("file not found: " % v)
    return f
