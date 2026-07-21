from collections import defaultdict

from django.db import transaction

from .models import NodeKind, TemplateNode
from .services import tree_rows


CYRILLIC_LETTERS = "абвгдежзиклмнопрстуфхцчшщэюя"


class OutlineError(ValueError):
    pass


def template_as_outline(template):
    rows = tree_rows(list(template.nodes.all()))
    return "\n".join(f"{'  ' * depth}{node.title}" for node, depth in rows)


def _letter_number(value):
    result = ""
    base = len(CYRILLIC_LETTERS)
    while value:
        value, remainder = divmod(value - 1, base)
        result = CYRILLIC_LETTERS[remainder] + result
    return result


def _code_for_path(path):
    parts = []
    for depth, position in enumerate(path):
        parts.append(_letter_number(position) if depth == 2 else str(position))
    code = ".".join(parts)
    return f"{code}." if len(path) == 1 else code


def parse_outline(text):
    entries = []
    stack = []
    source_lines = text.expandtabs(2).splitlines()
    for line_number, raw_line in enumerate(source_lines, start=1):
        if not raw_line.strip():
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        title = raw_line.strip()
        if not entries and indent:
            raise OutlineError("Первая строка не должна иметь отступ.")
        if len(title) > 300:
            raise OutlineError(f"Строка {line_number}: название длиннее 300 символов.")
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent_index = stack[-1][1] if stack else None
        depth = len(stack)
        if depth > 7:
            raise OutlineError(f"Строка {line_number}: поддерживается не больше 8 уровней вложенности.")
        entry = {
            "title": title,
            "indent": indent,
            "depth": depth,
            "parent_index": parent_index,
            "line_number": line_number,
        }
        entries.append(entry)
        stack.append((indent, len(entries) - 1))

    if not entries:
        raise OutlineError("Добавьте хотя бы один раздел или пункт.")
    if len(entries) > 1000:
        raise OutlineError("В шаблоне может быть не больше 1 000 строк.")

    child_counts = defaultdict(int)
    sibling_counts = defaultdict(int)
    paths = {}
    for index, entry in enumerate(entries):
        parent_index = entry["parent_index"]
        sibling_counts[parent_index] += 1
        position = sibling_counts[parent_index]
        parent_path = paths[parent_index] if parent_index is not None else []
        path = [*parent_path, position]
        paths[index] = path
        entry["position"] = position
        entry["code"] = _code_for_path(path)
        if parent_index is not None:
            child_counts[parent_index] += 1
    for index, entry in enumerate(entries):
        entry["kind"] = NodeKind.GROUP if child_counts[index] else NodeKind.CHECK
    return entries


def numbered_outline(entries):
    return "\n".join(f"{'  ' * entry['depth']}{entry['code']} {entry['title']}" for entry in entries)


@transaction.atomic
def replace_template_from_outline(template, entries):
    template.nodes.all().delete()
    node_map = {}
    for index, entry in enumerate(entries):
        parent_index = entry["parent_index"]
        node_map[index] = TemplateNode.objects.create(
            template=template,
            parent=node_map.get(parent_index),
            code=entry["code"],
            title=entry["title"],
            kind=entry["kind"],
            position=entry["position"],
        )
    return node_map
