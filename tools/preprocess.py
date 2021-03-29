#!/usr/bin/env python3
PROG_NAME = "preprocess.py"
HELP = """Preprocess tool for the pilot annotation

Usage:
    {p} [options] HTML

Options:
    -a, --annotator    Generate annotation files for annotators

    --data=DIR         Dir for data outputs [default: ./generated_data]
    --data-ref=DIR     Dir for reference data [default: ./data]
    --sources=DIR      Dir for HTML outputs [default: ./sources]

    -d, --debug        Show debug messages
    -q, --quiet        Show less messages

    -h, --help         Show this screen and exit
    -V, --version      Show version
""".format(p=PROG_NAME)
VERSION = "0.1.0"

# libraries
import os
import sys
import yaml
import json
import lxml.html
import unicodedata
from docopt import docopt
from pathlib import Path

sys.path.append('.')
from lib.cli import set_level
from lib.common import get_mi2idf

# use logger
import logging as log

log.Logger.set_level = set_level
logger = log.getLogger('preprocess')


def hex2surface(idf_hex):
    idf_text = bytes.fromhex(idf_hex).decode()
    surface = {'text': idf_text}

    if len(idf_text) < 2:
        surface['unicode_name'] = unicodedata.name(idf_text)

    return surface


def preprocess_html(tree, paper_id):
    root = tree.getroot()

    # drop unnecessary annotations
    for e in root.xpath('//annotation|//annotation-xml'):
        e.drop_tree()

    # tweak images
    for e in root.xpath('//img'):
        if 'ltx_graphics' in e.attrib.get('class', '').split(' '):
            e.attrib['src'] = '/static/img/{}/'.format(
                paper_id) + e.attrib['src']
            e.attrib['width'] = None
            e.attrib['height'] = None

    # add word span tags to text (directly) in <p> tags
    def split_words_into_span_tags(text, parent_id, idx):
        from lxml.html.builder import SPAN

        def word_span(w, p, i, c):
            s = SPAN(w)
            s.attrib['class'] = 'gd_word'
            s.attrib['id'] = '{}.{}.w{}'.format(p, i + 1, c)
            return s

        words = text.split(' ')
        word_cnt, spans = 1, []

        for w in words[:-1]:
            spans.extend([word_span(w, parent_id, idx, word_cnt), SPAN(' ')])
            word_cnt += 1

        if not words[-1] == '':
            spans.append(word_span(words[-1], parent_id, idx, word_cnt))

        return spans

    for e in root.xpath('//p|//figcaption'):
        # get texts and remove
        texts = [e.text]
        e.text = None
        for c in e.getchildren():
            texts.append(c.tail)
            c.tail = None

        # conpose span tags and add
        if e.tag == 'figcaption':
            parent_id = e.getparent().attrib['id']
        else:
            parent_id = e.attrib['id']
        spans = [
            split_words_into_span_tags(t, parent_id, i) if t else None
            for i, t in enumerate(texts)
        ]

        for i in range(len(spans) - 1, -1, -1):
            if not spans[i] is None:
                for s in reversed(spans[i]):
                    e.insert(i, s)

    # dirty hack: fix math markups
    if paper_id == '1808.02342':
        for e in root.xpath('//mtext'):
            if e.text == 'E':
                e.tag = 'mi'
                e.attrib['mathvariant'] = 'normal'

            if e.text == 'KL':
                e.tag = 'mi'

            if e.text.strip() == 'maximize':
                e.tag = 'mi'
                e.text = e.text.strip()

            if e.text == 'arg':
                # add "arg" to max/min
                mm = e.getnext().getnext().xpath(
                    './/mi[text()="max"]|.//mi[text()="min"]')[0]
                mm.text = 'arg' + mm.text

                # remove unnecessary elements
                e.getparent().remove(e)

        for e in root.xpath('//mo'):
            if e.text == '𝜃':
                e.tag = 'mi'
                e.text = 'θ'  # GREEK SMALL LETTER THETA

            if e.text == '𝑞':
                e.tag = 'mi'
                e.text = 'q'  # LATIN SMALL LETTER Q

        for e in root.xpath('//mi'):
            if e.text == 'old':
                e.tag = 'mtext'

    return tree


def observe_mi(tree, annotator, data_mcdict_ref):
    # initialize
    identifiers = set()
    occurences = dict()
    mi_attribs = set()

    # the process
    mi2idf = get_mi2idf(tree)
    root = tree.getroot()

    for e in root.xpath('//mi'):
        # get mi_id and idf
        mi_id = e.attrib.get('id')
        idf = mi2idf.get(mi_id)

        if not idf is None:
            idf_hex = idf['idf_hex']
            idf_var = idf['idf_var']
        else:
            continue

        # check for the attrib
        mi_attribs.update(e.attrib)

        # add to mi dict
        # Note: in --annotator, fill if the identifier has only a concept
        if annotator and len(
                data_mcdict_ref[idf_hex]['identifiers'][idf_var]) == 1:
            occurences[mi_id] = 0
        else:
            occurences[mi_id] = None

        identifiers.add((idf_hex, idf_var))

    return occurences, identifiers, mi_attribs


def idf2mc(idf_set):
    # initialize
    idf_dict = dict()

    # organize the identifiers
    for idf in idf_set:
        idf_hex, idf_var = idf
        if not idf_hex in idf_dict:
            idf_dict[idf_hex] = [idf_var]
        else:
            idf_dict[idf_hex].append(idf_var)

    idf_sorted = sorted(idf_dict.items(), key=lambda x: x[0])

    # construct a list of grounding functions
    return {
        idf[0]: {
            'surface': hex2surface(idf[0]),
            'identifiers': {
                v: [{
                    'args_type': [],
                    'arity': 0,
                    'description': None,
                }]
                for v in idf[1]
            }
        }
        for idf in idf_sorted
    }


def merge_mcdict(data_mcdict, data_mcdict_ref):
    for idf_hex, idfs in data_mcdict.items():
        idfs_ref = data_mcdict_ref.get(idf_hex,
                                       dict()).get('identifiers', None)

        if idfs_ref is None:
            continue

        for idf_var, idf in idfs['identifiers'].items():
            idf_ref = idfs_ref.get(idf_var, None)

            if idf_ref:
                idfs['identifiers'][idf_var] = idf_ref

    return data_mcdict


def main():
    # parse options
    args = docopt(HELP, version=VERSION)
    annotator = args['--annotator']

    # setup logger
    log_level = log.INFO
    if args['--quiet']:
        log_level = log.WARN
    if args['--debug']:
        log_level = log.DEBUG
    logger.set_level(log_level)

    # dirs and files
    data_dir = Path(args['--data'])
    data_dir_ref = Path(args['--data-ref'])
    sources_dir = Path(args['--sources'])

    html_in = Path(args['HTML'])
    paper_id = html_in.stem
    html_out = sources_dir / '{}.html'.format(paper_id)

    # make sure not to overwrite existing data unintentionally
    if data_dir.exists():
        logger.critical(
            'The data dir "{}" already exists. Abort!'.format(data_dir))
        sys.exit(1)

    # now prepare for the preprocess
    logger.info('Begin to preprocess Paper "{}"'.format(paper_id))

    os.mkdir(data_dir)
    anno_json = data_dir / '{}_anno.json'.format(paper_id)
    mcdict_yaml = data_dir / '{}_mcdict.yaml'.format(paper_id)

    data_mcdict_ref = dict()

    # for --annotator operation, reference data are required
    if annotator:
        anno_json_ref = data_dir_ref / '{}_anno.json'.format(paper_id)
        mcdict_yaml_ref = data_dir_ref / '{}_mcdict.yaml'.format(paper_id)

        if not anno_json_ref.exists() or not mcdict_yaml_ref.exists():
            logger.warn('For --annotator operation, reference data' \
                'files are required, but those not found.')
            logger.warn('Executing the default operation as fallback.')
            annotator = False

        else:
            with open(mcdict_yaml_ref) as f:
                data_mcdict_ref = yaml.load(f, Loader=yaml.FullLoader)

    # load and modify the HTML
    tree = lxml.html.parse(str(html_in))
    tree = preprocess_html(tree, paper_id)
    tree.write(str(html_out), pretty_print=True, encoding='utf-8')

    # extract formulae information
    occurences, identifiers, attribs = observe_mi(tree, annotator,
                                                  data_mcdict_ref)
    logger.info('#indentifiers: {}'.format(len(identifiers)))
    logger.info('mi attributes: {}'.format(', '.join(attribs)))

    # make the annotation structure
    data_anno = {
        'anno_version': '0.2',
        'annotator': 'NAME',
        'mi_anno': dict(),
    }
    for mi_id, concept_id in occurences.items():
        data_anno['mi_anno'][mi_id] = {
            'concept_id': concept_id,
            'sog': [],
        }

    # make the mcdict list
    data_mcdict = idf2mc(identifiers)

    # TODO: temporary, use the referential mcdict for annotators
    if annotator:
        data_mcdict = merge_mcdict(data_mcdict, data_mcdict_ref)

    # write the new generated data
    with open(anno_json, 'w') as f:
        json.dump(data_anno,
                  f,
                  ensure_ascii=False,
                  indent=4,
                  sort_keys=True,
                  separators=(',', ': '))
    with open(mcdict_yaml, 'w') as f:
        yaml.dump(data_mcdict,
                  f,
                  width=50,
                  encoding='utf-8',
                  allow_unicode=True)


if __name__ == '__main__':
    main()