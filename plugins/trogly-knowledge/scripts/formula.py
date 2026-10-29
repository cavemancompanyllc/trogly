"""The game's skill formulas ("3.9*DEF", "reletionEffectScalesByTargetHp&&DMG_MUL>=250000", ...) parsed and evaluated.

Formulas use numbers, names, function calls, `+ - * /`, comparisons, `&& || !`, and parentheses. Booleans are 1 and 0,
as in the game. Names and functions are looked up through a resolver, so the battle decides what each one means; a
name the battle doesn't know raises `Unsupported`, which the simulator reports instead of guessing. Never uses eval.
Stdlib only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable, Protocol

TOKEN = re.compile(r"\s*(?:(\d+\.\d*|\.\d+|\d+)|([A-Za-z_][A-Za-z_0-9]*)|(&&|\|\||==|!=|>=|<=|[-+*/()<>!,]))")

# Binding power of each binary operator; higher binds tighter.
BINARY = {"||": 1, "&&": 2, "==": 3, "!=": 3, "<": 4, "<=": 4, ">": 4, ">=": 4, "+": 5, "-": 5, "*": 6, "/": 6}
UNARY_POWER = 7


class FormulaError(Exception):
    """A formula the parser can't read."""


class Unsupported(Exception):
    """A formula names something the simulator doesn't model."""

    def __init__(self, name: str):
        super().__init__(name)
        self.name = name


class Resolver(Protocol):
    def value(self, name: str) -> float: ...

    def call(self, name: str, args: list[Any]) -> float: ...


@dataclass(frozen=True)
class Node:
    op: str
    args: tuple[Any, ...]


def tokenize(text: str) -> list[str]:
    tokens, position = [], 0
    text = text.strip()
    while position < len(text):
        match = TOKEN.match(text, position)
        if not match or match.end() == position:
            raise FormulaError(f"Can't read '{text[position:position + 12]}' in {text}")
        tokens.append(next(group for group in match.groups() if group is not None))
        position = match.end()
    return tokens


@lru_cache(maxsize=4096)
def parse(text: str) -> Node:
    tokens = tokenize(text)
    node, position = _expression(tokens, 0, 0)
    if position != len(tokens):
        raise FormulaError(f"Unexpected '{tokens[position]}' in {text}")
    return node


def _expression(tokens: list[str], position: int, min_power: int) -> tuple[Node, int]:
    left, position = _prefix(tokens, position)
    while position < len(tokens) and tokens[position] in BINARY and BINARY[tokens[position]] > min_power:
        op = tokens[position]
        right, position = _expression(tokens, position + 1, BINARY[op])
        left = Node(op, (left, right))
    return left, position


def _prefix(tokens: list[str], position: int) -> tuple[Node, int]:
    if position >= len(tokens):
        raise FormulaError("Formula ends too early")
    token = tokens[position]
    if token in ("!", "-", "+"):
        operand, position = _expression(tokens, position + 1, UNARY_POWER)
        return (Node("neg" if token == "-" else "not" if token == "!" else "pos", (operand,)), position)
    if token == "(":
        node, position = _expression(tokens, position + 1, 0)
        if position >= len(tokens) or tokens[position] != ")":
            raise FormulaError("Missing ')'")
        return node, position + 1
    if token[0].isdigit() or token[0] == ".":
        return Node("num", (float(token),)), position + 1
    if token[0].isalpha() or token[0] == "_":
        if position + 1 < len(tokens) and tokens[position + 1] == "(":
            args, position = [], position + 2
            while tokens[position] != ")":
                arg, position = _expression(tokens, position, 0)
                args.append(arg)
                if tokens[position] == ",":
                    position += 1
            return Node("call", (token, tuple(args))), position + 1
        return Node("name", (token,)), position + 1
    raise FormulaError(f"Unexpected '{token}'")


def evaluate(node: Node, resolver: Resolver) -> float:
    op, args = node.op, node.args
    if op == "num":
        return args[0]
    if op == "name":
        return float(resolver.value(args[0]))
    if op == "call":
        # Arguments that are bare names (an effect kind such as Burn) go through as names, not values.
        values = [arg.args[0] if arg.op == "name" else evaluate(arg, resolver) for arg in args[1]]
        return float(resolver.call(args[0], values))
    if op == "neg":
        return -evaluate(args[0], resolver)
    if op == "pos":
        return evaluate(args[0], resolver)
    if op == "not":
        return 0.0 if evaluate(args[0], resolver) else 1.0
    if op == "&&":
        return 1.0 if evaluate(args[0], resolver) and evaluate(args[1], resolver) else 0.0
    if op == "||":
        return 1.0 if evaluate(args[0], resolver) or evaluate(args[1], resolver) else 0.0
    left, right = evaluate(args[0], resolver), evaluate(args[1], resolver)
    return OPERATORS[op](left, right)


OPERATORS: dict[str, Callable[[float, float], float]] = {
    "+": lambda a, b: a + b,
    "-": lambda a, b: a - b,
    "*": lambda a, b: a * b,
    "/": lambda a, b: a / b if b else 0.0,
    "==": lambda a, b: float(a == b),
    "!=": lambda a, b: float(a != b),
    "<": lambda a, b: float(a < b),
    "<=": lambda a, b: float(a <= b),
    ">": lambda a, b: float(a > b),
    ">=": lambda a, b: float(a >= b),
}


def names_in(text: str) -> set[str]:
    """Every name and function a formula uses; for coverage reports."""
    found: set[str] = set()

    def walk(node: Node) -> None:
        if node.op == "name":
            found.add(node.args[0])
        elif node.op == "call":
            found.add(node.args[0])
            for arg in node.args[1]:
                if arg.op != "name":
                    walk(arg)
        elif node.op != "num":
            for arg in node.args:
                walk(arg)

    walk(parse(text))
    return found


def run(text: str, resolver: Resolver) -> float:
    return evaluate(parse(text), resolver)
