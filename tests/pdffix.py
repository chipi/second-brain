"""A minimal text PDF written by hand (Helvetica, one content stream per page), so tests need no PDF writer and no copyrighted file."""


def make_pdf(pages):
    """pages: a list of pages, each a list of text lines (an empty list is a blank page, like a scan without an OCR layer)."""
    objs = {1: b"<< /Type /Catalog /Pages 2 0 R >>", 3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"}
    kids = []
    for i, lines in enumerate(pages):
        page_no, stream_no = 4 + 2 * i, 5 + 2 * i
        kids.append(f"{page_no} 0 R")
        ops = ["BT /F1 10 Tf 12 TL 40 760 Td"]
        for ln in lines:
            ops.append("(" + ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ") Tj T*")
        ops.append("ET")
        data = "\n".join(ops).encode("latin-1")
        objs[stream_no] = b"<< /Length %d >>\nstream\n" % len(data) + data + b"\nendstream"
        objs[page_no] = (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {stream_no} 0 R "
                         "/Resources << /Font << /F1 3 0 R >> >> >>").encode()
    objs[2] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>".encode()
    out, offsets = bytearray(b"%PDF-1.4\n"), {}
    for n in sorted(objs):
        offsets[n] = len(out)
        out += b"%d 0 obj\n" % n + objs[n] + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for n in sorted(objs):
        out += b"%010d 00000 n \n" % offsets[n]
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)
