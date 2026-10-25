"""Existing text is read locally; scans/page-number overlays still need fallback."""
import math
import zlib

import pypdfium2 as pdfium
import pytest

from app.pdf_text import extract_page_text, extract_pages_text


def make_pdf(path, *, rotation=0, crop=False, image=False, text=True, second_blank=False):
    phrase = b"Volunteer event 2026 Alex Example 2024001234"
    body = b"BT /F1 18 Tf 70 700 Td (" + phrase + b") Tj ET" if text else b""
    if image:
        body = b"q 600 0 0 800 0 0 cm /Im1 Do Q" + (b" BT /F1 12 Tf 70 30 Td (Page 1 short footer only) Tj ET" if text else b"")
    extras = f" /Rotate {rotation}".encode() + (b" /CropBox [30 40 570 760]" if crop else b"")
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R" + (b" 7 0 R" if second_blank else b"") + b"] /Count " + (b"2" if second_blank else b"1") + b" >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources << /Font << /F1 4 0 R >> /XObject << /Im1 6 0 R >> >> /Contents 5 0 R" + extras + b" >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
               b"<< /Length " + str(len(body)).encode() + b" >>\nstream\n" + body + b"\nendstream"]
    image_data = zlib.compress(bytes([245,245,245]) * 10000)
    objects.append(b"<< /Type /XObject /Subtype /Image /Width 100 /Height 100 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode /Length " + str(len(image_data)).encode() + b" >>\nstream\n" + image_data + b"\nendstream")
    if second_blank:
        objects += [b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Contents 8 0 R >>", b"<< /Length 0 >>\nstream\n\nendstream"]
    data, offsets = bytearray(b"%PDF-1.4\n"), [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(str(index).encode() + b" 0 obj\n" + obj + b"\nendobj\n")
    start = len(data)
    data.extend(f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode())
    data.extend(b"".join(f"{offset:010} 00000 n \n".encode() for offset in offsets[1:]))
    data.extend(f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF".encode())
    path.write_bytes(data)
    return path


@pytest.mark.parametrize(('rotation','crop'), [(0,False),(90,False),(0,True),(270,True)])
def test_existing_text_and_char_boxes_match_rendered_page(tmp_path, rotation, crop):
    path = make_pdf(tmp_path/'native.pdf',rotation=rotation,crop=crop)
    with pdfium.PdfDocument(path) as doc:
        page=doc[0]
        bitmap=page.render(scale=2)
        image=bitmap.to_pil().convert('L')
        record={'id':'page-one','page_index':1,'width_px':image.width,'height_px':image.height}
        result=extract_page_text(path,record)
        assert result['usable'] is True
        assert 'Alex Example' in result['text'] and '2024001234' in result['text']
        words=[word for region in result['regions'] for word in region['extra_json']['words']]
        assert ''.join(word['text'] for word in words).endswith('2024001234')
        assert all(region['confidence'] is None for region in result['regions'])
        # Real ink must fall inside the mapped per-character bounds after crop
        # and intrinsic rotation, rather than just asserting a transform formula.
        with_ink=0
        for word in words:
            points=word['polygon']
            left,top=min(p['X'] for p in points),min(p['Y'] for p in points)
            right,bottom=max(p['X'] for p in points),max(p['Y'] for p in points)
            assert 0<=left<right<=image.width and 0<=top<bottom<=image.height
            region=image.crop((max(0,left-1),max(0,top-1),min(image.width,right+1),min(image.height,bottom+1)))
            with_ink+=region.getextrema()[0]<180
        assert with_ink>=len(words)*.95
        bitmap.close();page.close()


def test_image_scan_and_footer_overlay_do_not_count_as_complete_text_layer(tmp_path):
    record={'page_index':1,'width_px':1200,'height_px':1600}
    scanned=extract_page_text(make_pdf(tmp_path/'scan.pdf',image=True,text=False),record)
    assert scanned['usable'] is False and scanned['reason']=='NO_TEXT_LAYER'
    footer=extract_page_text(make_pdf(tmp_path/'footer.pdf',image=True),record)
    assert footer['usable'] is False and footer['reason']=='SPARSE_TEXT_OVER_IMAGE'


def test_batch_keeps_per_page_text_layer_decisions(tmp_path):
    path=make_pdf(tmp_path/'mixed.pdf',second_blank=True)
    pages=[{'id':'a','page_index':1,'width_px':1200,'height_px':1600},
           {'id':'b','page_index':2,'width_px':1200,'height_px':1600}]
    result=extract_pages_text(path,pages)
    assert result['a']['usable'] is True
    assert result['b']['usable'] is False
    assert result['b']['regions']==[]


def test_bad_document_and_out_of_range_page_are_safe_fallbacks(tmp_path):
    bad=tmp_path/'bad.pdf';bad.write_bytes(b'%PDF-not-valid')
    record={'page_index':1,'width_px':1200,'height_px':1600}
    assert extract_page_text(bad,record)['reason']=='INVALID_PDF'
    good=make_pdf(tmp_path/'good.pdf')
    assert extract_page_text(good,{**record,'page_index':0})['reason']=='INVALID_PAGE_INDEX'
