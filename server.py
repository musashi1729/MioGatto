#!/usr/bin/env python3

# libraries
from flask import Flask, request, redirect
import os
import sys
import re
import json
import yaml
import lxml.html
from copy import deepcopy


# preprocess mcdict
def convert_mcdict(data_mcdict):
    # description processor
    def process_math(math):
        def construct_mi(idf_text, idf_var, concept_id):
            mi = '<mi data-math-concept="{}"'.format(concept_id)

            if idf_var == 'roman':
                mi += ' mathvariant="normal">'
            else:
                mi += '>'

            mi += idf_text + '</mi>'

            return mi

        # protect references (@x)
        math = re.sub(r'(@\d+)', r'<mi>\1</mi>', math)

        # expand \gf
        rls = [(construct_mi(m.group(1), m.group(2),
                             int(m.group(3))), m.span())
               for m in re.finditer(r'\\gf{(.*?)}{(.*?)}{(\d*?)}', math)]
        for r in reversed(rls):
            s, e = r[1]
            math = math[:s] + r[0] + math[e:]

        return '<math>' + math + '</math>'

    def process_desc(desc):
        if not desc or not '$' in desc:
            return desc

        # process maths
        it = desc.split('$')
        desc_new = ''.join(
            [a + process_math(b) for a, b in zip(it[::2], it[1::2])])
        if len(it) % 2 != 0:
            desc_new += it[-1]

        return desc_new

    # initialize
    mcdict = dict()

    for idf_hex, data in data_mcdict.items():
        idf = data['identifiers']
        for concept_ls in idf.values():
            for concept in concept_ls:
                concept['description'] = process_desc(concept['description'])
        mcdict[idf_hex] = idf

    return mcdict


# generating demo HTML
def generate_html(paper_id, data_anno, tree):
    from lxml.html.builder import SPAN
    mi_anno = data_anno['mi_anno']

    # avoid destroying the original tree
    copied_tree = deepcopy(tree)
    root = copied_tree.getroot()

    # add data-math-concept for each mi element
    for mi in root.xpath('//mi'):
        mi_id = mi.get('id', None)
        if mi_id is None:
            continue

        concept_id = mi_anno.get(mi_id, dict()).get('concept_id')
        if concept_id is None:
            continue

        mi.attrib['data-math-concept'] = str(concept_id)

    # progress info
    nof_anno = len(mi_anno)
    nof_done = sum(1 for v in mi_anno.values() if not v['concept_id'] is None)
    progress = '{}/{} ({:.2f}%)'.format(nof_done, nof_anno,
                                        nof_done / nof_anno * 100)

    # add script and styles to the head
    extra_head_raw = '''
<script type="text/javascript" src="/static/vendor/jquery-3.4.1.min.js"></script>
<script type="text/javascript" src="/static/vendor/jquery-ui-1.12.1/jquery-ui.min.js"></script>
<link rel="stylesheet" href="/static/vendor/jquery-ui-1.12.1/jquery-ui.min.css">
<link rel="stylesheet" href="/static/style.css">
<script type="text/javascript" src="/static/client.js"></script>
'''
    head = root.xpath('head')[0]
    extra_head = list(lxml.html.fromstring(extra_head_raw).xpath('head')[0])
    head.extend(extra_head)

    # add the annotation sidebar
    body = root.xpath('body')[0]
    main_content = list(body)

    container_raw = '''
<div class="container">
<main class="main">
<div class="select-menu">
<input class="sog-add" type="submit" value="Add source">
<input class="sog-del" type="submit" value="Delete source">
</div>
</main>
<div class="sidebar">
<div class="sidebar-item">
<div class="sidebar-box">
<div class="sidebar-box-title">Document Information</div>
    <div class="sidebar-box-body">
    <p>paper ID: {}</p>
    <p>progress: {}</p>
    </div>
</div>
<div id="anno-box" class="sidebar-box">
</div>
</div>
</div>
</div>
'''.format(paper_id, progress)
    container = lxml.html.fromstring(container_raw)
    main = list(container)[0]
    main.extend(main_content)
    body.append(container)

    # finalize
    html = lxml.html.tostring(copied_tree, pretty_print=True, encoding='utf-8')

    return html.decode('utf-8')


def save_data(data_anno, anno_json):
    with open(anno_json, 'w') as f:
        json.dump(data_anno,
                  f,
                  ensure_ascii=False,
                  indent=4,
                  sort_keys=True,
                  separators=(',', ': '))
        f.write('\n')


def main():
    # the web app
    app = Flask(__name__)

    paper_id = sys.argv[1]

    # dirs and files
    source_html = './sources/{}.html'.format(paper_id)
    anno_json = './data/{}_anno.json'.format(paper_id)
    mcdict_yaml = './data/{}_mcdict.yaml'.format(paper_id)

    # initialize the annotation data
    with open(anno_json) as f:
        data_anno = json.load(f)
    if data_anno.get('anno_version') != '0.2':
        app.logger.warning('Annotation data version is incompatible')

    # parse html
    tree = lxml.html.parse(source_html)

    @app.route('/', methods=['GET', 'POST'])
    def index():
        return generate_html(paper_id, data_anno, tree)

    @app.route('/_concept', methods=['POST'])
    def action_concept():
        # register and save data_anno
        res = request.form

        if res.get('concept'):
            data_anno['mi_anno'][res['mi_id']]['concept_id'] = int(res['concept'])
            save_data(data_anno, anno_json)

        # redirect
        return redirect('/')

    @app.route('/_add_sog', methods=['POST'])
    def action_add_sog():
        res = request.form
        start_id, stop_id = res['start_id'], res['stop_id']
        cur_sog = data_anno['mi_anno'][res['mi_id']]['sog']

        # TODO: validate the span range
        if not [start_id, stop_id] in cur_sog:
            cur_sog.append([start_id, stop_id])

        save_data(data_anno, anno_json)

        # redirect
        return redirect('/')

    @app.route('/_delete_sog', methods=['POST'])
    def action_delete_sog():
        res = request.form
        start_id, stop_id = res['start_id'], res['stop_id']
        cur_sog = data_anno['mi_anno'][res['mi_id']]['sog']

        cur_sog.remove([start_id, stop_id])

        save_data(data_anno, anno_json)

        # redirect
        return redirect('/')

    @app.route('/mcdict.json', methods=['GET'])
    def mcdict_json():
        with open(mcdict_yaml) as f:
            data_mcdict = yaml.load(f, Loader=yaml.FullLoader)
        mcdict = convert_mcdict(data_mcdict)
        return json.dumps(mcdict,
                          ensure_ascii=False,
                          indent=4,
                          sort_keys=True,
                          separators=(',', ': '))

    @app.route('/sog.json', methods=['GET'])
    def sog_json():
        res = {'sog': []}

        for mi_id, anno in data_anno['mi_anno'].items():
            for sog in anno['sog']:
                res['sog'].append({
                    'mi_id': mi_id,
                    'start_id': sog[0],
                    'stop_id': sog[1]
                })

        return json.dumps(res,
                          ensure_ascii=False,
                          indent=4,
                          sort_keys=True,
                          separators=(',', ': '))

    app.debug = True
    app.run(host='localhost', port=4100)


if __name__ == '__main__':
    main()
