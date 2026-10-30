"""Render actual plan nodes as relational three-address-style intermediate code.

Each tN is a temporary relation, not a scalar variable. The text is a readable
serialization of the plan; Executor executes the plan object, not this text.
"""
from .errors import CompilerError


def literal(value):
    if value is None:
        return 'NULL'
    if isinstance(value, bool):
        return 'TRUE' if value else 'FALSE'
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return str(value)


def expression(node):
    kind = node['type']
    if kind == 'Literal':
        return literal(node['value'])
    if kind == 'Column':
        return node['name']
    if kind in {'Logical', 'Comparison', 'Arithmetic'}:
        return f"({expression(node['left'])} {node['operator']} {expression(node['right'])})"
    if kind == 'Not':
        return f"(NOT {expression(node['condition'])})"
    if kind == 'IsNull':
        return f"({expression(node['operand'])} IS {'NOT ' if node['negate'] else ''}NULL)"
    raise CompilerError('codegen', f'Cannot display expression {kind}.')


def generate_code(plan):
    """Postorder traversal retains expression, column and physical strategy details."""
    lines = []

    def emit(operation):
        temporary = f't{len(lines) + 1}'
        lines.append(f'{temporary} = {operation}')
        return temporary

    def visit(node):
        kind = node['type']
        if kind == 'EmptyResult':
            return emit('EMPTY_RELATION()')
        if kind in {'TableScan', 'SequentialScan', 'IndexLookup'}:
            columns = ', '.join(node['columns']) if 'columns' in node else '*'
            source = f"{node['table']} AS {node['alias']}"
            if kind == 'IndexLookup':
                operation = (f"INDEX_LOOKUP({source}, KEY {node['index_column']} = "
                             f"{literal(node['lookup_value'])}, COLUMNS [{columns}])")
            else:
                name = 'SCAN' if kind == 'TableScan' else 'SEQ_SCAN'
                operation = f'{name}({source}, COLUMNS [{columns}])'
            return emit(operation)
        if kind in {'Join', 'NestedLoopJoin', 'HashJoin'}:
            left, right = visit(node['left']), visit(node['right'])
            condition = expression(node['condition'])
            if kind == 'HashJoin':
                operation = (f"HASH_JOIN({left}, {right}, KEYS {node['left_key']} = "
                             f"{node['right_key']}, BUILD {node['build_side'].upper()}, ON {condition})")
            else:
                name = 'JOIN' if kind == 'Join' else 'NESTED_LOOP_JOIN'
                operation = f'{name}({left}, {right}, ON {condition})'
            return emit(operation)
        source = visit(node['input']) if 'input' in node else None
        if kind == 'Filter':
            return emit(f"FILTER({source}, {expression(node['condition'])})")
        if kind == 'Project':
            columns = ', '.join(f"{c['source']} AS {c['name']}" for c in node['columns'])
            return emit(f'PROJECT({source}, [{columns}])')
        if kind == 'Sort':
            keys = ', '.join(f"{expression(k['column'])} {k['direction']}" for k in node['keys'])
            return emit(f'SORT({source}, [{keys}])')
        if kind == 'Distinct':
            return emit(f'DISTINCT({source})')
        if kind == 'Limit':
            return emit(f"LIMIT({source}, {node['count']})")
        raise CompilerError('codegen', f'Cannot display plan operator {kind}.')

    result = visit(plan)
    return '\n'.join(lines + [f'RETURN {result}'])
