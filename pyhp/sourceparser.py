import py
from rpython.rlib.parsing.ebnfparse import parse_ebnf, make_parse_function
from rpython.rlib.parsing.parsing import ParseError
from rpython.rlib.parsing.tree import RPythonVisitor, Symbol
from rpython.rlib.rarithmetic import ovfcheck_float_to_int
from pyhp import pyhpdir
from pyhp import operations
from pyhp.scopes import Scope
from rpython.rlib.rstring import StringBuilder
from constants import CURLYVARIABLE

grammar_file = 'grammar.txt'
grammar = py.path.local(pyhpdir).join(grammar_file).read("rt")
try:
    regexs, rules, ToAST = parse_ebnf(grammar)
except ParseError, e:
    print e.nice_error_message(filename=grammar_file, source=grammar)
    raise
_parse = make_parse_function(regexs, rules, eof=True)


def parse(code):
    t = _parse(code)
    return ToAST().transform(t)


class Transformer(RPythonVisitor):
    """ Transforms AST from the obscure format given to us by the ennfparser
    to something easier to work with
    """
    BINOP_TO_CLS = {
        '+': operations.Plus,
        '-': operations.Sub,
        '*': operations.Mult,
        '/': operations.Division,
        '%': operations.Mod,
        '>': operations.Gt,
        '>=': operations.Ge,
        '<': operations.Lt,
        '<=': operations.Le,
        '.': operations.Plus,
        '&&': operations.And,
        '||': operations.Or,
        '==': operations.Eq,
        '>>': operations.Rsh,
        '>>>': operations.Ursh,
        '<<': operations.Lsh,
        '[': operations.Member,
        ',': operations.Comma,
    }
    UNOP_TO_CLS = {
        '!': operations.Not,
    }

    def __init__(self):
        self.funclists = []
        self.scopes = []
        self.depth = -1

    def visit_main(self, node):
        self.enter_scope()
        body = self.dispatch(node.children[0])
        scope = self.current_scope()
        final_scope = scope.finalize()
        return operations.Program(body, final_scope)

    def visit_arguments(self, node):
        nodes = [self.dispatch(child) for child in node.children[1:]]
        return operations.ArgumentList(nodes)

    def visit_sourceelements(self, node):
        self.funclists.append({})
        nodes = []
        for child in node.children:
            node = self.dispatch(child)
            if node is None:
                continue

            if isinstance(node, operations.Global):
                for node in node.nodes:
                    self.declare_global(node.get_literal())
                continue

            nodes.append(node)

        func_decl = self.funclists.pop()
        return operations.SourceElements(func_decl, nodes)

    def functioncommon(self, node, declaration=True):
        self.enter_scope()

        i = 0
        identifier, i = self.get_next_expr(node, i)
        parameters, i = self.get_next_expr(node, i)
        functionbody, i = self.get_next_expr(node, i)

        scope = self.current_scope()
        final_scope = scope.finalize()

        self.exit_scope()

        funcobj = operations.Function(identifier, functionbody, final_scope)

        if declaration:
            self.funclists[-1][identifier.get_literal()] = funcobj

        return funcobj

    def visit_functiondeclaration(self, node):
        self.functioncommon(node)
        return None

    def visit_formalparameterlist(self, node):
        for child in node.children:
            i = 0
            by_value = True
            if child.children[0].additional_info == '&':
                by_value = False
                i += 1
            variable = self.dispatch(child.children[i])
            self.declare_parameter(variable.get_literal(), by_value)
        return None

    def visit_statementlist(self, node):
        block = self.dispatch(node.children[0])
        return operations.StatementList(block)

    def binaryop(self, node):
        left = self.dispatch(node.children[0])
        for i in range((len(node.children) - 1) // 2):
            op = node.children[i * 2 + 1]
            right = self.dispatch(node.children[i * 2 + 2])
            result = self.BINOP_TO_CLS[op.additional_info](left, right)
            left = result
        return left
    visit_logicalorexpression = binaryop
    visit_logicalandexpression = binaryop
    visit_stringjoinexpression = binaryop
    visit_relationalexpression = binaryop
    visit_equalityexpression = binaryop
    visit_additiveexpression = binaryop
    visit_multiplicativeexpression = binaryop
    visit_shiftexpression = binaryop
    visit_expression = binaryop
    visit_memberexpression = binaryop

    def visit_unaryexpression(self, node):
        op = node.children[0]
        child = self.dispatch(node.children[1])
        if op.additional_info in ['++', '--']:
            return self._dispatch_assignment(child, op.additional_info, 'pre')
        return self.UNOP_TO_CLS[op.additional_info](child)

    def literalop(self, node):
        value = node.children[0].additional_info
        if value == "true":
            return operations.Boolean(True)
        elif value == "false":
            return operations.Boolean(False)
        else:
            return operations.Null()
    visit_nullliteral = literalop
    visit_booleanliteral = literalop

    def visit_numericliteral(self, node):
        number = ""
        for node in node.children:
            number += node.additional_info
        try:
            f = float(number)
            i = ovfcheck_float_to_int(f)
            if i != f:
                return operations.ConstantFloat(f)
            else:
                return operations.ConstantInt(i)
        except (ValueError, OverflowError):
            return operations.ConstantFloat(float(node.additional_info))

    def visit_expressionstatement(self, node):
        return operations.ExprStatement(self.dispatch(node.children[0]))

    def visit_printstatement(self, node):
        return operations.Print(self.dispatch(node.children[1]))

    def visit_globalstatement(self, node):
        nodes = [self.dispatch(child) for child in node.children[1].children]
        return operations.Global(nodes)

    def visit_constantexpression(self, node):
        node = node.children[0]
        name = node.additional_info
        return operations.Constant(name)

    def visit_callexpression(self, node):
        left = self.dispatch(node.children[0])
        nodelist = node.children[1:]
        while nodelist:
            currnode = nodelist.pop(0)
            if isinstance(currnode, Symbol):
                raise NotImplementedError("Not implemented")
            else:
                right = self.dispatch(currnode)
                left = operations.Call(left, right)

        return left

    def _dispatch_assignment(self, left, atype, prepost):
        is_post = prepost == 'post'
        if self.is_variable(left):
            return operations.AssignmentOperation(left, None, atype, is_post)
        elif self.is_member(left):
            return operations.MemberAssignmentOperation(left, None, atype,
                                                        is_post)
        else:
            raise Exception("invalid lefthand expression")

    def visit_postfixexpression(self, node):
        op = node.children[1]
        child = self.dispatch(node.children[0])
        # all postfix expressions are assignments
        return self._dispatch_assignment(child, op.additional_info, 'post')

    def visit_arrayliteral(self, node):
        l = [self.dispatch(child) for child in node.children[1:]]
        return operations.Array(l)

    def visit_block(self, node):
        l = [self.dispatch(child) for child in node.children[1:]]
        return operations.Block(l)

    def visit_assignmentexpression(self, node):
        left = self.dispatch(node.children[0])
        operation = node.children[1].additional_info
        right = self.dispatch(node.children[2])

        if self.is_variable(left):
            return operations.AssignmentOperation(left, right, operation)
        elif self.is_member(left):
            return operations.MemberAssignmentOperation(left, right, operation)
        else:
            raise Exception("invalid lefthand expression")

    def visit_ifstatement(self, node):
        condition = self.dispatch(node.children[0])
        ifblock = self.dispatch(node.children[1])
        if len(node.children) > 2:
            elseblock = self.dispatch(node.children[2])
        else:
            elseblock = None
        return operations.If(condition, ifblock, elseblock)

    def visit_conditionalexpression(self, node):
        condition = self.dispatch(node.children[0])
        truepart = self.dispatch(node.children[2])
        falsepart = self.dispatch(node.children[3])
        return operations.If(condition, truepart, falsepart)

    def visit_iterationstatement(self, node):
        return self.dispatch(node.children[0])

    def visit_whiles(self, node):
        itertype = node.children[0].additional_info
        if itertype == 'while':
            condition = self.dispatch(node.children[1])
            block = self.dispatch(node.children[2])
            return operations.While(condition, block)
        else:
            raise NotImplementedError("Unknown while version %s" % (itertype,))

    def visit_regularfor(self, node):
        i = 1
        setup, i = self.get_next_expr(node, i)
        condition, i = self.get_next_expr(node, i)
        if isinstance(condition, operations.Null):
            condition = operations.Boolean(True)
        update, i = self.get_next_expr(node, i)
        body, i = self.get_next_expr(node, i)

        if setup is None:
            setup = operations.EmptyExpression()
        if condition is None:
            condition = operations.Boolean(True)
        if update is None:
            update = operations.EmptyExpression()
        if body is None:
            body = operations.EmptyExpression()

        return operations.For(setup, condition, update, body)

    def visit_foreach(self, node):
        array = self.dispatch(node.children[1])
        variable = self.dispatch(node.children[2])
        body = self.dispatch(node.children[3])
        return operations.Foreach(array, None, variable, body)

    def visit_keyforeach(self, node):
        array = self.dispatch(node.children[1])
        key = self.dispatch(node.children[2])
        variable = self.dispatch(node.children[3])
        body = self.dispatch(node.children[4])
        return operations.Foreach(array, key, variable, body)

    def visit_breakstatement(self, node):
        if len(node.children) > 0:
            count = self.dispatch(node.children[0])
        else:
            count = None
        return operations.Break(count)

    def visit_continuestatement(self, node):
        if len(node.children) > 0:
            count = self.dispatch(node.children[0])
        else:
            count = None
        return operations.Continue(count)

    def visit_returnstatement(self, node):
        if len(node.children) > 0:
            value = self.dispatch(node.children[0])
        else:
            value = None
        return operations.Return(value)

    def visit_IDENTIFIERNAME(self, node):
        name = node.additional_info
        return operations.Identifier(name)

    def visit_VARIABLENAME(self, node):
        name = node.additional_info
        index = self.declare_variable(name)
        return operations.VariableIdentifier(name, index)

    def string(self, node):
        string = node.additional_info
        string = string_unescape(string)
        string, single_quotes = string_unquote(string)
        if single_quotes:
            return operations.ConstantString(string)
        else:
            string, has_variable = double_quote_string_parse(self, string)
            if not has_variable:
                return string[0]
            return operations.StringSubstitution(string)
    visit_DOUBLESTRING = string
    visit_SINGLESTRING = string

    def get_next_expr(self, node, i):
        if isinstance(node.children[i], Symbol) and \
           node.children[i].additional_info in [';', ')', '(', '}']:
            return None, i+1
        else:
            return self.dispatch(node.children[i]), i+2

    def is_variable(self, obj):
        from pyhp.operations import VariableIdentifier
        return isinstance(obj, VariableIdentifier)

    def is_member(self, obj):
        from pyhp.operations import Member
        return isinstance(obj, Member)

    def enter_scope(self):
        self.depth = self.depth + 1

        new_scope = Scope()
        self.scopes.append(new_scope)

    def declare_variable(self, symbol):
        s = symbol
        idx = self.scopes[-1].add_variable(s)
        return idx

    def declare_global(self, symbol):
        s = symbol
        idx = self.scopes[-1].add_global(s)
        return idx

    def declare_parameter(self, symbol, by_value):
        s = symbol
        idx = self.scopes[-1].add_parameter(s, by_value)
        return idx

    def exit_scope(self):
        self.depth = self.depth - 1
        self.scopes.pop()

    def current_scope(self):
        try:
            return self.scopes[-1]
        except IndexError:
            return None


def string_unquote(string):
    s = string
    single_quotes = True
    if s.startswith('"'):
        assert s.endswith('"')
        single_quotes = False
    else:
        assert s.startswith("'")
        assert s.endswith("'")
    s = s[:-1]
    s = s[1:]

    return s, single_quotes


def double_quote_string_parse(transformer, string):
    s_ = []

    has_variable = False
    for part in CURLYVARIABLE.split(string):
        if len(part) > 0 and part[0] == '$':
            has_variable = True
            # force variable to be a valid php expression "$a;"
            parsed = parse(part + ';')
            expression = parsed.children[0].children[0].children[0]
            part = transformer.dispatch(expression)
        elif part == '' and len(s_) > 0:
            continue
        else:
            part = operations.ConstantString(part)
        s_.append(part)

    return s_, has_variable


def string_unescape(string):
    s = string
    size = len(string)

    if size == 0:
        return ''

    builder = StringBuilder(size)
    pos = 0
    while pos < size:
        ch = s[pos]

        # Non-escape characters are interpreted as Unicode ordinals
        if ch != '\\':
            builder.append(ch)
            pos += 1
            continue

        # - Escapes
        pos += 1
        if pos >= size:
            message = "\\ at end of string"
            raise Exception(message)

        ch = s[pos]
        pos += 1
        # \x escapes
        if ch == '\n':
            pass
        elif ch == '\\':
            builder.append('\\')
        elif ch == '\'':
            builder.append('\'')
        elif ch == '\"':
            builder.append('\"')
        elif ch == 'b':
            builder.append('\b')
        elif ch == 'f':
            builder.append('\f')
        elif ch == 't':
            builder.append('\t')
        elif ch == 'n':
            builder.append('\n')
        elif ch == 'r':
            builder.append('\r')
        elif ch == 'v':
            builder.append('\v')
        elif ch == 'a':
            builder.append('\a')
        else:
            builder.append(ch)

    return builder.build()
