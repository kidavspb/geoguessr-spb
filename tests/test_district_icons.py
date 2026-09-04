from pathlib import Path
import xml.etree.ElementTree as ET

from districts import DISTRICT_IDS


ICON_DIR = Path(__file__).resolve().parents[1] / 'static' / 'img' / 'district-icons'
SVG_NAMESPACE = 'http://www.w3.org/2000/svg'


def test_district_icon_set_is_complete_and_sanitized():
    """Каждому canonical району соответствует один автономный vector path."""
    files = sorted(ICON_DIR.glob('*.svg'))
    assert {path.stem for path in files} == set(DISTRICT_IDS)

    for path in files:
        source = path.read_text(encoding='utf-8')
        root = ET.fromstring(source)
        assert root.tag == f'{{{SVG_NAMESPACE}}}svg'

        view_box = [float(value) for value in root.attrib['viewBox'].split()]
        assert len(view_box) == 4
        assert view_box[2] > 0
        assert view_box[2] == view_box[3]

        children = list(root)
        assert len(children) == 1
        icon = children[0]
        assert icon.tag == f'{{{SVG_NAMESPACE}}}path'
        assert icon.attrib['fill'] == '#2FC6F5'
        assert icon.attrib['fill-rule'] == 'evenodd'
        assert icon.attrib['clip-rule'] == 'evenodd'
        assert len(icon.attrib['d']) > 100

        assert '<rect' not in source
        assert '<image' not in source
        assert '<script' not in source
        assert 'href=' not in source
