"""Optional, consent-gated diagram proposal. The user must confirm all links."""
from __future__ import annotations
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import base64
import json
import os
import subprocess
import shutil

from PIL import Image

from .onboarding import Draft, Place, Connection


def _page_images(content: bytes, kind: str, pages: int):
    if kind != 'pdf':
        images = [content]
    else:
        converter = shutil.which('pdftoppm')
        if not converter:
            raise ValueError('PDF rendering is unavailable; trace the diagram manually')
        images = []
        with TemporaryDirectory() as directory:
            source = Path(directory) / 'drawing.pdf'
            source.write_bytes(content)
            for page in range(1, pages + 1):
                target = Path(directory) / f'page{page}'
                subprocess.run([converter, '-f', str(page), '-l', str(page),
                                '-scale-to', '1400', '-singlefile', '-png',
                                str(source), str(target)], capture_output=True,
                               timeout=20, check=True)
                images.append(target.with_suffix('.png').read_bytes())
    for data in images:
        with Image.open(BytesIO(data)) as img:
            img = img.convert('RGB')
            img.thumbnail((1400, 1400))
            out = BytesIO()
            img.save(out, format='JPEG', quality=80)
            yield out.getvalue()


def propose_draft(draft: Draft, content: bytes):
    key, model = os.getenv('OPENAI_API_KEY'), os.getenv('WATERGUARD_VISION_MODEL')
    if not key or not model:
        raise ValueError('Vision model is not configured; trace the diagram manually')
    from openai import OpenAI
    images = list(_page_images(content, draft.document['type'], draft.document['pages']))
    prompt = ("Return only JSON with nodes and links. Nodes have label, kind "
              "(source, tank, junction, outlet), x and y on a 0-800 by 0-480 canvas, "
              "and optional elevation_m, demand_lps, head_m. Links have start_index, "
              "end_index, kind (pipe, pump, valve), optional length_m and diameter_mm. "
              "Use zero-based node indices. Include a connection only when clearly visible. "
              "Never invent dimensions. This is a proposal that a homeowner will review.")
    blocks = [{'type': 'input_text', 'text': prompt}]
    blocks.extend({'type': 'input_image', 'image_url': 'data:image/jpeg;base64,' +
                   base64.b64encode(img).decode()} for img in images)
    response = OpenAI(api_key=key, timeout=40).responses.create(
        model=model, input=[{'role': 'user', 'content': blocks}])
    raw = response.output_text.strip()
    if raw.startswith('```'):
        raw = raw.split('\n', 1)[1].rsplit('```', 1)[0]
    proposal = json.loads(raw)
    draft.nodes = []
    draft.links = []
    index_to_id = {}
    for i, item in enumerate(proposal.get('nodes', [])[:30]):
        kind = item.get('kind')
        if kind not in ('source', 'tank', 'junction', 'outlet'):
            continue
        kwargs = {key: item.get(key) for key in ('elevation_m', 'demand_lps', 'head_m')}
        draft.nodes.append(Place(id=f'Point{i+1}', name=str(item.get('label') or f'Point {i+1}')[:60],
                                 kind=kind, x=float(item.get('x') or 100), y=float(item.get('y') or 100),
                                 provenance={key: 'diagram' for key, value in item.items() if value is not None},
                                 **kwargs))
        index_to_id[i] = draft.nodes[-1].id
    for i, item in enumerate(proposal.get('links', [])[:40]):
        start, end = item.get('start_index'), item.get('end_index')
        if not isinstance(start, int) or not isinstance(end, int) or start == end:
            continue
        if start not in index_to_id or end not in index_to_id:
            continue
        kind = item.get('kind', 'pipe')
        if kind not in ('pipe', 'pump', 'valve'):
            continue
        draft.links.append(Connection(id=f'Link{i+1}', name=str(item.get('label') or f'Connection {i+1}')[:60],
                                      kind=kind, start=index_to_id[start], end=index_to_id[end],
                                      length_m=item.get('length_m'), diameter_mm=item.get('diameter_mm'),
                                      provenance={'start': 'inferred', 'end': 'inferred',
                                                  'length_m': 'diagram' if item.get('length_m') is not None else 'unknown',
                                                  'diameter_mm': 'diagram' if item.get('diameter_mm') is not None else 'unknown'}))
    draft.document['interpretation'] = 'AI proposal; confirm all connections and values'
    return draft
