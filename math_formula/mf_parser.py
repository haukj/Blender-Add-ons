import math
from enum import IntEnum, auto
from typing import Literal, cast, overload

from . import ast_defs
from .backends.type_defs import DataType, string_to_data_type
from .scanner import Scanner, Token, TokenType


class Precedence(IntEnum):
    NONE = 0
    ASSIGNMENT = auto()  # =
    OR = auto()  # or
    AND = auto()  # and
    NOT = auto()  # not
    COMPARISON = auto()  # < > <= >= == !=
    TERM = auto()  # + -
    FACTOR = auto()  # * / %
    UNARY = auto()  # -
    EXPONENT = auto()  # ^ **
    ATTRIBUTE = auto()  # .
    CALL = auto()  # () {}
    PRIMARY = auto()


class Error:
    def __init__(self, token: Token, message: str) -> None:
        self.token = token
        self.message = message

    def __repr__(self) -> str:
        return self.__str__()

    def __str__(self) -> str:
        return f"{self.message}"


class ParseRule:
    def __init__(self, prefix, infix, precedence: Precedence) -> None:
        self.prefix = prefix
        self.infix = infix
        self.precedence = precedence


class Parser:
    def __init__(self, source: str) -> None:
        self.scanner = Scanner(source)
        self.token_buffer: list[Token] = []
        self.current: Token = self.scanner.scan_token()
        self.previous: Token = self.current
        self.had_error: bool = False
        self.panic_mode: bool = False
        self.curr_node: ast_defs.stmt | None = None
        self.errors: list[Error] = []

    def parse(self) -> ast_defs.Module:
        module = ast_defs.Module()
        while not self.match(TokenType.EOL):
            statement = self.declaration()
            if statement is not None:
                module.body.append(statement)
            if self.panic_mode:
                self.synchronize()
        return module

    def error_at_current(self, message: str) -> None:
        self.error_at(self.current, message)

    def error(self, message: str) -> None:
        self.error_at(self.previous, message)

    def error_at(self, token: Token, message: str) -> None:
        if self.panic_mode:
            return
        self.panic_mode = True
        error = f"line:{token.line}:{token.col}: Error"
        if token.token_type == TokenType.EOL:
            error += " at end:"
        elif token.token_type == TokenType.ERROR:
            pass
        else:
            error += f' at "{token.lexeme}":'
        self.errors.append(Error(token, f"{error} {message}"))
        self.had_error = True

    def consume(self, token_type: TokenType, message: str) -> None:
        if self.check(token_type):
            self.advance()
            return

        self.error_at_current(message)

    def add_tokens(self, tokens: list[Token]) -> None:
        first = tokens[0]
        self.token_buffer.append(self.current)
        # Need to reverse because we pop later
        self.token_buffer += list(reversed(tokens[1:]))

        self.current = first

    def advance(self) -> None:
        self.previous = self.current

        # Get tokens till not an error
        while True:
            if self.token_buffer != []:
                self.current = self.token_buffer.pop()
            else:
                self.current = self.scanner.scan_token()
            if self.current.token_type != TokenType.ERROR:
                break
            token = self.current
            token_str = token.lexeme
            message = cast(str, token.error)
            self.error_at(
                Token(
                    token_str,
                    TokenType.ERROR,
                    line=token.line,
                    col=token.col,
                    start=token.start,
                ),
                message,
            )

    def get_rule(self, token_type: TokenType) -> ParseRule:
        return rules[token_type.value]

    @overload
    def parse_precedence(
        self, precedence: Literal[Precedence.ASSIGNMENT], skip_advance=False
    ) -> ast_defs.stmt | None:
        ...

    @overload
    def parse_precedence(
        self,
        precedence: Literal[Precedence.OR]
        | Literal[Precedence.AND]
        | Literal[Precedence.NOT]
        | Literal[Precedence.COMPARISON]
        | Literal[Precedence.TERM]
        | Literal[Precedence.FACTOR]
        | Literal[Precedence.UNARY]
        | Literal[Precedence.EXPONENT]
        | Literal[Precedence.ATTRIBUTE]
        | Literal[Precedence.CALL]
        | Literal[Precedence.PRIMARY],
        skip_advance=False,
    ) -> ast_defs.expr | None:
        ...

    def parse_precedence(
        self, precedence: Precedence, skip_advance=False
    ) -> ast_defs.stmt | None:
        if not skip_advance:
            self.advance()
        prefix_rule = self.get_rule(self.previous.token_type).prefix
        if prefix_rule is None:
            self.error("Expect expression.")
            return None
        can_assign = precedence.value <= Precedence.ASSIGNMENT.value
        prefix_rule(self, can_assign)
        while (
            precedence.value <= self.get_rule(self.current.token_type).precedence.value
        ):
            self.advance()
            infix_rule = self.get_rule(self.previous.token_type).infix
            infix_rule(self, can_assign)
        if can_assign and self.match(TokenType.EQUAL):
            self.error("Invalid assignment target.")
        if self.curr_node is None:
            self.error("Expected expression with a value.")
        if self.curr_node is not None and not can_assign:
            assert isinstance(self.curr_node, ast_defs.expr)
        return self.curr_node

    def check(self, token_type: TokenType) -> bool:
        return self.current.token_type == token_type

    def match(self, token_type: TokenType) -> bool:
        if not self.check(token_type):
            return False
        self.advance()
        return True

    def expression(self) -> ast_defs.expr | None:
        # Assignment is not a valid expression
        return self.parse_precedence(Precedence.OR)

    def statement(self) -> ast_defs.stmt | None:
        # Assignment is a valid statement
        node = self.parse_precedence(Precedence.ASSIGNMENT)
        # Get optional semicolon at end of expression
        self.match(TokenType.SEMICOLON)
        self.curr_node = None
        return node

    def parse_type(self) -> DataType:
        self.consume(TokenType.COLON, "Expected type after argument name.")
        if self.match(TokenType.IDENTIFIER):
            if self.previous.lexeme in string_to_data_type:
                return string_to_data_type[self.previous.lexeme]
            else:
                self.error(f"Invalid data type: {self.previous.lexeme}.")
        else:
            self.error("Expected a data type")
        return DataType.UNKNOWN

    def parse_arg(self) -> ast_defs.arg:
        self.consume(TokenType.IDENTIFIER, "Expect argument name")
        token = self.previous
        name = token.lexeme
        var_type = self.parse_type()
        default = None
        if self.match(TokenType.EQUAL):
            default = self.expression()
        return ast_defs.arg(token, name, var_type, default)

    def out(self) -> ast_defs.Out | None:
        # Something like:
        # out x = 10;
        # out x,y,z = 10;
        # out x,_,z = position();
        token = self.previous
        targets: list[ast_defs.Name | None] = []
        message = 'Expect variable name or "_" after "out".'
        while not self.match(TokenType.EQUAL):
            if self.match(TokenType.IDENTIFIER):
                targets.append(ast_defs.Name(self.previous, self.previous.lexeme))
            elif self.match(TokenType.UNDERSCORE):
                targets.append(None)
            else:
                self.error_at_current(message)
            if not self.match(TokenType.COMMA):
                self.consume(TokenType.EQUAL, 'Expected "=". ')
                break
        if targets == []:
            self.error_at(token, message)
        value = self.expression()
        if value is None:
            # Bubble up the error
            return None
        self.match(TokenType.SEMICOLON)  # Optional semicolon
        self.curr_node = None
        return ast_defs.Out(token, targets, value)

    def parse_func_structure(
        self,
    ) -> tuple[list[ast_defs.arg], list[ast_defs.stmt], list[ast_defs.arg]]:
        self.consume(TokenType.LEFT_PAREN, 'Expect "(" after name.')
        args = []
        while not self.check(TokenType.RIGHT_PAREN):
            args.append(self.parse_arg())
            if not self.match(TokenType.COMMA):
                break
        self.consume(TokenType.RIGHT_PAREN, 'Expect closing ")".')
        returns = []
        if self.match(TokenType.ARROW):
            returns.append(self.parse_arg())
            while self.match(TokenType.COMMA):
                returns.append(self.parse_arg())
        self.consume(TokenType.LEFT_BRACE, "Expect function body.")
        body = []
        while not (self.check(TokenType.RIGHT_BRACE) or self.match(TokenType.EOL)):
            if (stmt := self.declaration()) is None:
                continue
            body.append(stmt)
        self.consume(TokenType.RIGHT_BRACE, 'Expect closing "}".')
        self.curr_node = None
        return args, body, returns

    def function_def(self) -> ast_defs.FunctionDef:
        if not (self.match(TokenType.IDENTIFIER) or self.match(TokenType.STRING)):
            self.error("Expected function name.")
        token = self.previous
        name = token.lexeme
        if token.token_type == TokenType.STRING:
            name = name[1:-1]
        args, body, returns = self.parse_func_structure()
        return ast_defs.FunctionDef(token, name, args, body, returns)

    def nodegroup_def(self) -> ast_defs.NodegroupDef:
        if not (self.match(TokenType.IDENTIFIER) or self.match(TokenType.STRING)):
            self.error("Expected node group name.")
        token = self.previous
        name = token.lexeme
        if token.token_type == TokenType.STRING:
            name = name[1:-1]
        args, body, returns = self.parse_func_structure()
        return ast_defs.NodegroupDef(token, name, args, body, returns)

    def parse_int(self) -> int:
        if self.match(TokenType.MINUS):
            self.consume(TokenType.INT, "Expected an integer")
            if self.panic_mode:
                return 0
            return -int(self.previous.lexeme)
        self.consume(TokenType.INT, "Expected an integer")
        if self.panic_mode:
            return 0
        return int(self.previous.lexeme)

    def loop(self) -> ast_defs.Loop:
        token = self.previous
        var = None
        if self.match(TokenType.IDENTIFIER):
            var = ast_defs.Name(self.previous, self.previous.lexeme)
            self.consume(TokenType.EQUAL, 'Expect "=" after loop variable.')
        start = 1
        end = self.parse_int()
        if self.match(TokenType.ARROW):
            # Explicit start and end values given
            start = end
            end = self.parse_int()
        self.consume(TokenType.LEFT_BRACE, "Expect loop body.")
        body = []
        while not (self.check(TokenType.RIGHT_BRACE) or self.match(TokenType.EOL)):
            if (stmt := self.declaration()) is None:
                continue
            body.append(stmt)
        self.consume(TokenType.RIGHT_BRACE, 'Expect closing "}".')
        self.curr_node = None
        return ast_defs.Loop(token, var, start, end, body)

    def declaration(self) -> ast_defs.stmt | None:
        node: ast_defs.stmt | None = None
        if self.match(TokenType.OUT):
            node = self.out()
        elif self.match(TokenType.FUNCTION):
            node = self.function_def()
        elif self.match(TokenType.NODEGROUP):
            node = self.nodegroup_def()
        elif self.match(TokenType.LOOP):
            node = self.loop()
        else:
            node = self.statement()
        return node

    def call_args(self) -> tuple[list[ast_defs.expr], list[ast_defs.Keyword]] | None:
        pos_args = []
        keyword_args = []
        if not self.check(TokenType.RIGHT_PAREN):
            while (
                self.match(TokenType.COMMA)
                or self.previous.token_type == TokenType.LEFT_PAREN
            ):
                # Check for a keyword argument
                if self.match(TokenType.IDENTIFIER):
                    if self.check(TokenType.EQUAL):
                        arg_token = self.previous
                        self.advance()  # Get rid of the "="
                        value = self.expression()
                        if value is None:
                            return None
                        keyword_args.append(
                            ast_defs.Keyword(arg_token, arg_token.lexeme, value)
                        )
                        # Now all arguments should be keyword arguments
                        error_msg = (
                            "No positional arguments allowed after keyword argument."
                        )
                        while self.match(TokenType.COMMA):
                            self.consume(TokenType.IDENTIFIER, error_msg)
                            arg_token = self.previous
                            self.consume(TokenType.EQUAL, 'Expect "=" after keyword.')
                            value = self.expression()
                            if value is None:
                                return None
                            keyword_args.append(
                                ast_defs.Keyword(arg_token, arg_token.lexeme, value)
                            )
                    else:
                        # Not a keyword so normal argument
                        if (
                            pos_arg := self.parse_precedence(Precedence.OR, True)
                        ) is None:
                            return None
                        pos_args.append(pos_arg)
                else:
                    if (pos_arg := self.expression()) is None:
                        return None
                    pos_args.append(pos_arg)

        self.consume(TokenType.RIGHT_PAREN, 'Expect ")" after arguments.')
        return pos_args, keyword_args

    def synchronize(self) -> None:
        self.panic_mode = False
        while self.previous.token_type != TokenType.EOL:
            if self.previous.token_type == TokenType.SEMICOLON:
                return
            if self.current.token_type in (
                TokenType.OUT,
                TokenType.FUNCTION,
                TokenType.NODEGROUP,
            ):
                return
            self.advance()


def make_int(self: Parser, can_assign: bool) -> None:
    token = self.previous
    value = int(token.lexeme)
    self.curr_node = ast_defs.Constant(token, value, DataType.INT)


def make_float(self: Parser, can_assign: bool) -> None:
    token = self.previous
    value = float(token.lexeme)
    self.curr_node = ast_defs.Constant(token, value, DataType.FLOAT)


def python(self: Parser, can_assign: bool) -> None:
    token = self.previous
    expression = token.lexeme[1:]
    value = 0
    try:
        value = eval(expression, vars(math))  # type: ignore
    except (SyntaxError, NameError, TypeError, ZeroDivisionError) as err:
        self.error(f"Invalid python syntax: {err}.")
    try:
        converted_value = float(value)
    except ValueError as err:
        self.error(f"Expected result of python expression to be a number: {err}.")
        converted_value = 0.0
    self.curr_node = ast_defs.Constant(token, converted_value, DataType.FLOAT)


def default(self: Parser, can_assign: bool) -> None:
    self.curr_node = ast_defs.Constant(self.previous, None, DataType.DEFAULT)


def identifier(self: Parser, can_assign: bool) -> None:
    identifier_token = self.previous
    name = identifier_token.lexeme
    if can_assign and (self.check(TokenType.EQUAL) or self.match(TokenType.COMMA)):
        targets: list[ast_defs.Name | None] = [ast_defs.Name(identifier_token, name)]
        while not self.check(TokenType.EQUAL):
            if self.match(TokenType.IDENTIFIER):
                targets.append(ast_defs.Name(self.previous, self.previous.lexeme))
            elif self.match(TokenType.UNDERSCORE):
                targets.append(None)
            else:
                self.error_at_current('Expect variable name or "_" separated by ",". ')
            if not self.match(TokenType.COMMA):
                break
        self.consume(TokenType.EQUAL, 'Expect "="')
        equal_token = self.previous
        value = self.expression()
        if value is None:
            return
        self.curr_node = ast_defs.Assign(equal_token, targets, value)
    else:
        self.curr_node = ast_defs.Name(identifier_token, name)


def string(self: Parser, can_assign: bool) -> None:
    token = self.previous
    self.curr_node = ast_defs.Constant(token, token.lexeme[1:-1], DataType.STRING)


def boolean(self: Parser, can_assign: bool) -> None:
    token = self.previous
    self.curr_node = ast_defs.Constant(token, token.lexeme == "true", DataType.BOOL)


def grouping(self: Parser, can_assign: bool) -> None:
    self.curr_node = self.expression()
    self.consume(TokenType.RIGHT_PAREN, 'Expect closing ")" after expression.')


def unary(self: Parser, can_assign: bool) -> None:
    operator_token = self.previous
    operator_type = operator_token.token_type
    # Compile the operand
    operand = self.parse_precedence(Precedence.UNARY)
    if operand is None:
        return None

    unaryop: ast_defs.unaryop | None = None
    if operator_type == TokenType.MINUS:
        unaryop = ast_defs.USub(operator_token)
    elif operator_type == TokenType.NOT:
        unaryop = ast_defs.Not(operator_token)
    else:
        # Shouldn't happen
        assert False, "Unreachable code"

    self.curr_node = ast_defs.UnaryOp(operator_token, unaryop, operand)


def make_vector(self: Parser, can_assign: bool) -> None:
    bracket_token = self.previous
    x: ast_defs.expr | None
    y: ast_defs.expr | None
    z: ast_defs.expr | None
    x = y = z = ast_defs.Constant(bracket_token, 0, DataType.DEFAULT)
    if not self.match(TokenType.RIGHT_BRACE):
        x = self.expression()
        if self.match(TokenType.COMMA):
            y = self.expression()
        if self.match(TokenType.COMMA):
            z = self.expression()
        self.consume(TokenType.RIGHT_BRACE, 'Expect closing "}".')
    if x is None or y is None or z is None:
        return None
    self.curr_node = ast_defs.Vec3(bracket_token, x, y, z)


def group_name(self: Parser, can_assign: bool) -> None:
    token = self.previous
    func = ast_defs.Name(token, token.lexeme[2:-1])
    self.consume(TokenType.LEFT_PAREN, 'Expect "(" after node group name.')
    ret = self.call_args()
    if ret is None:
        return None
    pos_args, keyword_args = ret
    # Special handling: convert join_geometry(a, b, c) -> join_geometry([a, b, c]) ...but dont tell mf add-on about it...
    if isinstance(func, ast_defs.Name) and func.id == "join_geometry":
        if len(pos_args) > 1:
            from .ast_defs import ListLiteral
            list_expr = ListLiteral(token, pos_args)
            pos_args = [list_expr]
    self.curr_node = ast_defs.Call(token, func, pos_args, keyword_args)


def call(self: Parser, can_assign: bool) -> None:
    token = self.previous
    func = self.curr_node
    assert func is not None, "Error in the parser"
    if not (isinstance(func, ast_defs.Name) or isinstance(func, ast_defs.Attribute)):
        self.error("Expected function name to call.")
        return
    ret = self.call_args()
    if ret is None:
        return
    pos_args, keyword_args = ret
    # Special handling: convert join_geometry(a, b, c) -> join_geometry([a, b, c])
    if isinstance(func, ast_defs.Name) and func.id == "join_geometry":
        if len(pos_args) > 1:
            from .ast_defs import ListLiteral
            list_expr = ListLiteral(token, pos_args)
            pos_args = [list_expr]
    self.curr_node = ast_defs.Call(token, func, pos_args, keyword_args)


def dot(self: Parser, can_assign: bool) -> None:
    token = self.previous
    self.consume(TokenType.IDENTIFIER, 'Expect output name or function call after ".".')
    identifier_token = self.previous
    value = self.curr_node
    if value is None:
        return
    else:
        assert isinstance(value, ast_defs.expr)
    self.curr_node = ast_defs.Attribute(token, value, identifier_token.lexeme)


def binary(self: Parser, can_assign: bool) -> None:
    operator_token = self.previous
    operator_type = operator_token.token_type
    left = self.curr_node
    rule = self.get_rule(operator_type)
    right: ast_defs.expr | None = self.parse_precedence(
        Precedence(rule.precedence.value + 1)  # type: ignore
    )
    if left is None or right is None:
        return
    else:
        assert isinstance(left, ast_defs.expr) and isinstance(right, ast_defs.expr)

    # math: + - / * % > < **
    operation: ast_defs.operator | None = None
    if operator_type == TokenType.PLUS:
        operation = ast_defs.Add(operator_token)
    elif operator_type == TokenType.MINUS:
        operation = ast_defs.Sub(operator_token)
    elif operator_type == TokenType.SLASH:
        operation = ast_defs.Div(operator_token)
    elif operator_type == TokenType.STAR:
        operation = ast_defs.Mult(operator_token)
    elif operator_type == TokenType.PERCENT:
        operation = ast_defs.Mod(operator_token)
    elif operator_type in (TokenType.STAR_STAR, TokenType.HAT):
        operation = ast_defs.Pow(operator_token)
    elif operator_type == TokenType.GREATER:
        operation = ast_defs.Gt(operator_token)
    elif operator_type == TokenType.GREATER_EQUAL:
        operation = ast_defs.GtE(operator_token)
    elif operator_type == TokenType.LESS:
        operation = ast_defs.Lt(operator_token)
    elif operator_type == TokenType.LESS_EQUAL:
        operation = ast_defs.LtE(operator_token)
    elif operator_type == TokenType.EQUAL_EQUAL:
        operation = ast_defs.Eq(operator_token)
    elif operator_type == TokenType.BANG_EQUAL:
        operation = ast_defs.NotEq(operator_token)
    elif operator_type == TokenType.AND:
        operation = ast_defs.And(operator_token)
    elif operator_type == TokenType.OR:
        operation = ast_defs.Or(operator_token)
    else:
        assert False, "Unreachable code"
    self.curr_node = ast_defs.BinOp(operator_token, left, operation, right)


rules: list[ParseRule] = [
    ParseRule(grouping, call, Precedence.CALL),  # LEFT_PAREN
    ParseRule(None, None, Precedence.NONE),  # RIGHT_PAREN
    ParseRule(None, None, Precedence.NONE),  # LEFT_SQUARE_BRACKET
    ParseRule(None, None, Precedence.NONE),  # RIGHT_SQUARE_BRACKET
    ParseRule(make_vector, None, Precedence.NONE),  # LEFT_BRACE
    ParseRule(None, None, Precedence.NONE),  # RIGHT_BRACE
    ParseRule(None, None, Precedence.NONE),  # COMMA
    ParseRule(None, dot, Precedence.ATTRIBUTE),  # DOT
    ParseRule(None, None, Precedence.NONE),  # SEMICOLON
    ParseRule(None, None, Precedence.NONE),  # EQUAL
    ParseRule(unary, binary, Precedence.TERM),  # MINUS
    ParseRule(None, binary, Precedence.TERM),  # PLUS
    ParseRule(None, binary, Precedence.FACTOR),  # PERCENT
    ParseRule(None, binary, Precedence.FACTOR),  # SLASH
    ParseRule(None, binary, Precedence.EXPONENT),  # HAT
    ParseRule(None, binary, Precedence.COMPARISON),  # GREATER
    ParseRule(None, binary, Precedence.COMPARISON),  # LESS
    ParseRule(None, None, Precedence.NONE),  # COLON
    ParseRule(default, None, Precedence.NONE),  # UNDERSCORE
    ParseRule(None, binary, Precedence.FACTOR),  # STAR
    ParseRule(None, binary, Precedence.EXPONENT),  # STAR_STAR
    ParseRule(None, None, Precedence.NONE),  # ARROW
    ParseRule(None, binary, Precedence.COMPARISON),  # LESS_EQUAL
    ParseRule(None, binary, Precedence.COMPARISON),  # GREATER_EQUAL
    ParseRule(None, binary, Precedence.COMPARISON),  # EQUAL_EQUAL
    ParseRule(None, binary, Precedence.COMPARISON),  # BANG_EQUAL
    ParseRule(identifier, None, Precedence.NONE),  # IDENTIFIER
    ParseRule(make_int, None, Precedence.NONE),  # INT
    ParseRule(make_float, None, Precedence.NONE),  # FLOAT
    ParseRule(python, None, Precedence.NONE),  # PYTHON
    ParseRule(string, None, Precedence.NONE),  # STRING
    ParseRule(group_name, None, Precedence.NONE),  # GROUP_NAME
    ParseRule(None, None, Precedence.NONE),  # OUT
    ParseRule(None, None, Precedence.NONE),  # FUNCTION
    ParseRule(None, None, Precedence.NONE),  # NODEGROUP
    ParseRule(None, None, Precedence.NONE),  # LOOP
    ParseRule(boolean, None, Precedence.NONE),  # TRUE
    ParseRule(boolean, None, Precedence.NONE),  # FALSE
    ParseRule(unary, None, Precedence.NOT),  # NOT
    ParseRule(None, binary, Precedence.OR),  # OR
    ParseRule(None, binary, Precedence.AND),  # AND
    ParseRule(None, None, Precedence.NONE),  # ERROR
    ParseRule(None, None, Precedence.NONE),  # EOL
]
assert len(rules) == TokenType.EOL.value + 1, "Didn't handle all tokens!"
