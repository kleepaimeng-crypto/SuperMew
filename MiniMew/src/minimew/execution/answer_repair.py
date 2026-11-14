"""格式纠正只能调整约定分区、空白和引用；不能借此改写正文。"""

from minimew.evidence import DOCUMENT_HEADING, REFERENCE, SUPPLEMENT_HEADING


def body_characters(text):
    body = "\n".join(
        line for line in text.splitlines() if line not in {DOCUMENT_HEADING, SUPPLEMENT_HEADING}
    )
    return "".join(REFERENCE.sub("", body).split())
