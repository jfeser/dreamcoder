# -*- coding: utf-8 -*-

from type import *
from utilities import *

from time import time
import math


class InferenceFailure(Exception):
    pass


class ShiftFailure(Exception):
    pass


class ParseFailure(Exception):
    pass


class RunFailure(Exception):
    pass


class Program(object):
    def __repr__(self): return str(self)

    def __ne__(self, o): return not (self == o)

    def __str__(self): return self.show(False)

    def canHaveType(self, t):
        try:
            context, actualType = self.inferType(Context.EMPTY, [], {})
            context, t = t.instantiate(context)
            context.unify(t, actualType)
            return True
        except UnificationFailure as e:
            return False

    def betaNormalForm(self):
        n = self
        while True:
            np = n.betaReduce()
            if np is None: return n
            n = np

    def infer(self):
        try:
            return self.inferType(Context.EMPTY, [], {})[1].canonical()
        except UnificationFailure as e:
            raise InferenceFailure(self, e)

    def uncurry(self):
        t = self.infer()
        a = len(t.functionArguments())
        e = self
        existingAbstractions = 0
        while e.isAbstraction:
            e = e.body
            existingAbstractions += 1
        newAbstractions = a - existingAbstractions
        assert newAbstractions >= 0

        # e is the body stripped of abstractions. we are going to pile
        # some more lambdas at the front, so free variables in e
        # (which were bound to the stripped abstractions) need to be
        # shifted by the number of abstractions that we will be adding
        e = e.shift(newAbstractions)

        for n in reversed(range(newAbstractions)):
            e = Application(e, Index(n))
        for _ in range(a):
            e = Abstraction(e)

        assert self.infer() == e.infer(), \
            "FATAL: uncurry has a bug. %s : %s, but uncurried to %s : %s" % (self, self.infer(),
                                                                             e, e.infer())
        return e

    def wellTyped(self):
        try:
            self.infer()
            return True
        except InferenceFailure:
            return False

    def runWithArguments(self, xs):
        f = self.evaluate([])
        for x in xs:
            f = f(x)
        return f

    def applicationParses(self): yield self, []

    def applicationParse(self): return self, []

    @property
    def closed(self):
        for surroundingAbstractions, child in self.walk():
            if isinstance(child, FragmentVariable):
                return False
            if isinstance(child, Index) and child.free(
                    surroundingAbstractions):
                return False
        return True

    @property
    def numberOfFreeVariables(expression):
        n = 0
        for surroundingAbstractions, child in expression.walk():
            # Free variable
            if isinstance(child, Index) and child.free(
                    surroundingAbstractions):
                n = max(n, child.i - surroundingAbstractions + 1)
        return n

    def freeVariables(self):
        for surroundingAbstractions, child in self.walk():
            if child.isIndex and child.i >= surroundingAbstractions:
                yield child.i - surroundingAbstractions

    @property
    def isIndex(self): return False

    @property
    def isApplication(self): return False

    @property
    def isAbstraction(self): return False

    @property
    def isPrimitive(self): return False

    @property
    def isInvented(self): return False

    @property
    def isHole(self): return True

    @staticmethod
    def parse(s):
        e, s = Program._parse(s.strip())
        if s != "":
            raise ParseFailure(s)
        return e

    @staticmethod
    def _parse(s):
        while len(s) > 0 and s[0].isspace():
            s = s[1:]
        for p in [
                Application,
                Abstraction,
                Index,
                Invented,
                FragmentVariable,
                Hole,
                Primitive]:
            try:
                return p._parse(s)
            except ParseFailure:
                continue
        raise ParseFailure(s)


class Application(Program):
    '''Function application'''

    def __init__(self, f, x):
        self.f = f
        self.x = x
        self.hashCode = None
        self.isConditional = (not isinstance(f,int)) and \
                             f.isApplication and \
                             f.f.isApplication and \
                             f.f.f.isPrimitive and \
                             f.f.f.name == "if"
        if self.isConditional:
            self.falseBranch = x
            self.trueBranch = f.x
            self.branch = f.f.x
        else:
            self.falseBranch = None
            self.trueBranch = None
            self.branch = None

    def betaReduce(self):
        # See if either the function or the argument can be reduced
        f = self.f.betaReduce()
        if f is not None: return Application(f,self.x)
        x = self.x.betaReduce()
        if x is not None: return Application(self.f,x)

        # Neither of them could be reduced. Is this not a redex?
        if not self.f.isAbstraction: return None

        # Perform substitution
        b = self.f.body
        v = self.x
        return b.substitute(Index(0), v.shift(1)).shift(-1)

    @property
    def isApplication(self): return True

    def __eq__(
        self,
        other): return isinstance(
        other,
        Application) and self.f == other.f and self.x == other.x

    def __hash__(self):
        if self.hashCode is None:
            self.hashCode = hash((hash(self.f), hash(self.x)))
        return self.hashCode

    """Because Python3 randomizes the hash function, we need to never pickle the hash"""
    def __getstate__(self):
        return self.f, self.x, self.isConditional, self.falseBranch, self.trueBranch, self.branch
    def __setstate__(self, state):
        self.f, self.x, self.isConditional, self.falseBranch, self.trueBranch, self.branch = state
        self.hashCode = None

    def visit(self,
              visitor,
              *arguments,
              **keywords): return visitor.application(self,
                                                      *arguments,
                                                      **keywords)

    def show(self, isFunction):
        if isFunction:
            return "%s %s" % (self.f.show(True), self.x.show(False))
        else:
            return "(%s %s)" % (self.f.show(True), self.x.show(False))

    def evaluate(self, environment):
        if self.isConditional:
            if self.branch.evaluate(environment):
                return self.trueBranch.evaluate(environment)
            else:
                return self.falseBranch.evaluate(environment)
        else:
            return self.f.evaluate(environment)(self.x.evaluate(environment))

    def inferType(self, context, environment, freeVariables):
        (context, ft) = self.f.inferType(context, environment, freeVariables)
        (context, xt) = self.x.inferType(context, environment, freeVariables)
        (context, returnType) = context.makeVariable()
        context = context.unify(ft, arrow(xt, returnType))
        return (context, returnType.apply(context))

    def applicationParses(self):
        yield self, []
        for f, xs in self.f.applicationParses():
            yield f, xs + [self.x]

    def applicationParse(self):
        f, xs = self.f.applicationParse()
        return f, xs + [self.x]

    def shift(self, offset, depth=0):
        return Application(self.f.shift(offset, depth),
                           self.x.shift(offset, depth))

    def substitute(self, old, new):
        if self == old:
            return new
        return Application(
            self.f.substitute(
                old, new), self.x.substitute(
                old, new))

    def walkUncurried(self, d=0):
        yield d, self
        f, xs = self.applicationParse()
        yield from f.walkUncurried(d)
        for x in xs:
            yield from x.walkUncurried(d)

    def walk(self, surroundingAbstractions=0):
        yield surroundingAbstractions, self
        yield from self.f.walk(surroundingAbstractions)
        yield from self.x.walk(surroundingAbstractions)

    def size(self): return self.f.size() + self.x.size()

    @staticmethod
    def _parse(s):
        while len(s) > 0 and s[0].isspace():
            s = s[1:]
        if s == "" or s[0] != '(':
            raise ParseFailure(s)
        s = s[1:]

        xs = []
        while True:
            x, s = Program._parse(s)
            xs.append(x)
            while len(s) > 0 and s[0].isspace():
                s = s[1:]
            if s == "":
                raise ParseFailure(s)
            if s[0] == ")":
                s = s[1:]
                break
        e = xs[0]
        for x in xs[1:]:
            e = Application(e, x)
        return e, s


class Index(Program):
    '''
    deBruijn index: https://en.wikipedia.org/wiki/De_Bruijn_index
    These indices encode variables.
    '''

    def __init__(self, i):
        self.i = i

    def show(self, isFunction): return "$%d" % self.i

    def __eq__(self, o): return isinstance(o, Index) and o.i == self.i

    def __hash__(self): return self.i

    def visit(self,
              visitor,
              *arguments,
              **keywords): return visitor.index(self,
                                                *arguments,
                                                **keywords)

    def evaluate(self, environment):
        return environment[self.i]

    def inferType(self, context, environment, freeVariables):
        if self.bound(len(environment)):
            return (context, environment[self.i].apply(context))
        else:
            i = self.i - len(environment)
            if i in freeVariables:
                return (context, freeVariables[i].apply(context))
            context, variable = context.makeVariable()
            freeVariables[i] = variable
            return (context, variable)

    def shift(self, offset, depth=0):
        # bound variable
        if self.bound(depth):
            return self
        else:  # free variable
            i = self.i + offset
            if i < 0:
                raise ShiftFailure()
            return Index(i)

    def betaReduce(self): return None

    def substitute(self, old, new):
        if old == self:
            return new
        else:
            return self

    def walk(self, surroundingAbstractions=0): yield surroundingAbstractions, self

    def walkUncurried(self, d=0): yield d, self

    def size(self): return 1

    def free(self, surroundingAbstractions):
        '''Is this index a free variable, given that it has surroundingAbstractions lambda's around it?'''
        return self.i >= surroundingAbstractions

    def bound(self, surroundingAbstractions):
        '''Is this index a bound variable, given that it has surroundingAbstractions lambda's around it?'''
        return self.i < surroundingAbstractions

    @property
    def isIndex(self): return True

    @staticmethod
    def _parse(s):
        while len(s) > 0 and s[0].isspace():
            s = s[1:]
        if s == "" or s[0] != '$':
            raise ParseFailure(s)
        s = s[1:]
        n = ""
        while s != "" and s[0].isdigit():
            n += s[0]
            s = s[1:]
        if n == "":
            raise ParseFailure(s)
        return Index(int(n)), s


class Abstraction(Program):
    '''Lambda abstraction. Creates a new function.'''

    def __init__(self, body):
        self.body = body
        self.hashCode = None

    @property
    def isAbstraction(self): return True

    def __eq__(self, o): return isinstance(
        o, Abstraction) and o.body == self.body

    def __hash__(self):
        if self.hashCode is None:
            self.hashCode = hash((hash(self.body),))
        return self.hashCode

        """Because Python3 randomizes the hash function, we need to never pickle the hash"""
    def __getstate__(self):
        return self.body
    def __setstate__(self, state):
        self.body = state
        self.hashCode = None


    def visit(self,
              visitor,
              *arguments,
              **keywords): return visitor.abstraction(self,
                                                      *arguments,
                                                      **keywords)

    def show(self, isFunction):
        return "(lambda %s)" % (self.body.show(False))

    def evaluate(self, environment):
        return lambda x: self.body.evaluate([x] + environment)

    def betaReduce(self):
        b = self.body.betaReduce()
        if b is None: return None
        return Abstraction(b)

    def inferType(self, context, environment, freeVariables):
        (context, argumentType) = context.makeVariable()
        (context, returnType) = self.body.inferType(
            context, [argumentType] + environment, freeVariables)
        return (context, arrow(argumentType, returnType).apply(context))

    def shift(self, offset, depth=0):
        return Abstraction(self.body.shift(offset, depth + 1))

    def substitute(self, old, new):
        if self == old:
            return new
        old = old.shift(1)
        new = new.shift(1)
        return Abstraction(self.body.substitute(old, new))

    def walk(self, surroundingAbstractions=0):
        yield surroundingAbstractions, self
        yield from self.body.walk(surroundingAbstractions + 1)

    def walkUncurried(self, d=0):
        yield d, self
        yield from self.body.walkUncurried(d + 1)

    def size(self): return self.body.size()

    @staticmethod
    def _parse(s):
        if s.startswith('(\\'):
            s = s[2:]
        elif s.startswith('(lambda'):
            s = s[len('(lambda'):]
        elif s.startswith('(\u03bb'):
            s = s[len('(\u03bb'):]
        else:
            raise ParseFailure(s)
        while len(s) > 0 and s[0].isspace():
            s = s[1:]

        b, s = Program._parse(s)
        while len(s) > 0 and s[0].isspace():
            s = s[1:]
        if s == "" or s[0] != ')':
            raise ParseFailure(s)
        s = s[1:]
        return Abstraction(b), s


class Primitive(Program):
    GLOBALS = {}

    def __init__(self, name, ty, value):
        self.tp = ty
        self.name = name
        self.value = value
        if name not in Primitive.GLOBALS:
            Primitive.GLOBALS[name] = self

    @property
    def isPrimitive(self): return True

    def __eq__(self, o): return isinstance(
        o, Primitive) and o.name == self.name

    def __hash__(self): return hash(self.name)

    def visit(self,
              visitor,
              *arguments,
              **keywords): return visitor.primitive(self,
                                                    *arguments,
                                                    **keywords)

    def show(self, isFunction): return self.name

    def evaluate(self, environment): return self.value

    def betaReduce(self): return None

    def inferType(self, context, environment, freeVariables):
        return self.tp.instantiate(context)

    def shift(self, offset, depth=0): return self

    def substitute(self, old, new):
        if self == old:
            return new
        else:
            return self

    def walk(self, surroundingAbstractions=0): yield surroundingAbstractions, self

    def walkUncurried(self, d=0): yield d, self

    def size(self): return 1

    @staticmethod
    def _parse(s):
        while len(s) > 0 and s[0].isspace():
            s = s[1:]
        name = ""
        while s != "" and not s[0].isspace() and s[0] not in '()':
            name += s[0]
            s = s[1:]
        if name in Primitive.GLOBALS:
            return Primitive.GLOBALS[name], s
        raise ParseFailure(s)


class Invented(Program):
    '''New invented primitives'''

    def __init__(self, body):
        self.body = body
        self.tp = self.body.infer()
        self.hashCode = None

    @property
    def isInvented(self): return True

    def show(self, isFunction): return "#%s" % (self.body.show(False))

    def visit(self,
              visitor,
              *arguments,
              **keywords): return visitor.invented(self,
                                                   *arguments,
                                                   **keywords)

    def __eq__(self, o): return isinstance(o, Invented) and o.body == self.body

    def __hash__(self):
        if self.hashCode is None:
            self.hashCode = hash((0, hash(self.body)))
        return self.hashCode

    """Because Python3 randomizes the hash function, we need to never pickle the hash"""
    def __getstate__(self):
        return self.body, self.tp
    def __setstate__(self, state):
        self.body, self.tp = state
        self.hashCode = None

    def evaluate(self, e): return self.body.evaluate([])

    def betaReduce(self): return self.body

    def inferType(self, context, environment, freeVariables):
        return self.tp.instantiate(context)

    def shift(self, offset, depth=0): return self

    def substitute(self, old, new):
        if self == old:
            return new
        else:
            return self

    def walk(self, surroundingAbstractions=0): yield surroundingAbstractions, self

    def walkUncurried(self, d=0): yield d, self

    def size(self): return 1

    @staticmethod
    def _parse(s):
        while len(s) > 0 and s[0].isspace():
            s = s[1:]
        if not s.startswith('#'):
            raise ParseFailure(s)
        s = s[1:]
        b, s = Program._parse(s)
        return Invented(b), s


class FragmentVariable(Program):
    def __init__(self): pass

    def show(self, isFunction): return "??"

    def __eq__(self, o): return isinstance(o, FragmentVariable)

    def __hash__(self): return 42

    def visit(self, visitor, *arguments, **keywords):
        return visitor.fragmentVariable(self, *arguments, **keywords)

    def evaluate(self, e):
        raise Exception('Attempt to evaluate fragment variable')

    def betaReduce(self):
        raise Exception('Attempt to beta reduce fragment variable')

    def inferType(self, context, environment, freeVariables):
        return context.makeVariable()

    def shift(self, offset, depth=0):
        raise Exception('Attempt to shift fragment variable')

    def substitute(self, old, new):
        if self == old:
            return new
        else:
            return self

    def match(
            self,
            context,
            expression,
            holes,
            variableBindings,
            environment=[]):
        surroundingAbstractions = len(environment)
        try:
            context, variable = context.makeVariable()
            holes.append(
                (variable, expression.shift(-surroundingAbstractions)))
            return context, variable
        except ShiftFailure:
            raise MatchFailure()

    def walk(self, surroundingAbstractions=0): yield surroundingAbstractions, self

    def walkUncurried(self, d=0): yield d, self

    def size(self): return 1

    @staticmethod
    def _parse(s):
        while len(s) > 0 and s[0].isspace():
            s = s[1:]
        if s.startswith('??'):
            return FragmentVariable.single, s[2:]
        if s.startswith('?'):
            return FragmentVariable.single, s[1:]
        raise ParseFailure(s)

class Hole(Program):
    def __init__(self): pass

    def show(self, isFunction): return "<HOLE>"

    @property
    def isHole(self): return True

    def __eq__(self, o): return isinstance(o, Hole)

    def __hash__(self): return 42

    def evaluate(self, e):
        raise Exception('Attempt to evaluate hole')

    def betaReduce(self):
        raise Exception('Attempt to beta reduce hole')

    def inferType(self, context, environment, freeVariables):
        return context.makeVariable()

    def shift(self, offset, depth=0):
        raise Exception('Attempt to shift fragment variable')

    def walk(self, surroundingAbstractions=0): yield surroundingAbstractions, self

    def walkUncurried(self, d=0): yield d, self

    def size(self): return 1

    @staticmethod
    def _parse(s):
        while len(s) > 0 and s[0].isspace():
            s = s[1:]
        if s.startswith('<HOLE>'):
            return Hole.single, s[len('<HOLE>'):]
        raise ParseFailure(s)


Hole.single = Hole()


class ShareVisitor(object):
    def __init__(self):
        self.primitiveTable = {}
        self.inventedTable = {}
        self.indexTable = {}
        self.applicationTable = {}
        self.abstractionTable = {}

    def invented(self, e):
        body = e.body.visit(self)
        i = id(body)
        if i in self.inventedTable:
            return self.inventedTable[i]
        new = Invented(body)
        self.inventedTable[i] = new
        return new

    def primitive(self, e):
        if e.name in self.primitiveTable:
            return self.primitiveTable[e.name]
        self.primitiveTable[e.name] = e
        return e

    def index(self, e):
        if e.i in self.indexTable:
            return self.indexTable[e.i]
        self.indexTable[e.i] = e
        return e

    def application(self, e):
        f = e.f.visit(self)
        x = e.x.visit(self)
        fi = id(f)
        xi = id(x)
        i = (fi, xi)
        if i in self.applicationTable:
            return self.applicationTable[i]
        new = Application(f, x)
        self.applicationTable[i] = new
        return new

    def abstraction(self, e):
        body = e.body.visit(self)
        i = id(body)
        if i in self.abstractionTable:
            return self.abstractionTable[i]
        new = Abstraction(body)
        self.abstractionTable[i] = new
        return new

    def execute(self, e):
        return e.visit(self)


class Mutator:
    """Perform local mutations to an expr"""

    def __init__(self, grammar, fn):
        """Fn yields expressions from a type and loss."""
        self.fn = fn
        self.grammar = grammar
        self.history = []

    def enclose(self, expr):
        for h in self.history[::-1]:
            expr = h(expr)
        return expr

    def invented(self, e, tp):
        for expr in self.fn(tp, -self.grammar.expression2likelihood[e]):
            yield self.enclose(expr)

    def primitive(self, e, tp):
        for expr in self.fn(tp, -self.grammar.expression2likelihood[e]):
            yield self.enclose(expr)

    def index(self, e, tp):
        for expr in self.fn(tp, -self.grammar.logVariable):
            yield self.enclose(expr)

    def application(self, e, tp):
        self.history.append(lambda expr: Application(expr, e.x))
        f_tp = arrow(e.x.infer(), tp)
        yield from e.f.visit(self, f_tp)
        self.history[-1] = lambda expr: Application(e.f, expr)
        x_tp = inferArg(tp, e.f.infer())
        yield from e.x.visit(self, x_tp)
        self.history.pop()
        for expr in self.fn(tp, -self.logLikelihood(tp, e)):
            yield self.enclose(expr)

    def abstraction(self, e, tp):
        self.history.append(lambda expr: Abstraction(expr))
        yield from e.body.visit(self, tp.arguments[1])
        self.history.pop()
        # we don't try turning the abstraction into something else, because
        # that other thing will just be an abstraction

    def execute(self, e, tp):
        yield from e.visit(self, tp)

    def logLikelihood(self, tp, e):
        summary = None
        try:
            summary = self.grammar.closedLikelihoodSummary(tp, e, silent=True)
        except AssertionError:
            pass
        if summary is not None:
            return summary.logLikelihood(self.grammar)
        else:
            depth, tmpTp = 0, tp
            while tmpTp.isArrow() and not isinstance(e, Abstraction):
                depth, tmpTp = depth + 1, tmpTp.arguments[1]
            old = e
            for _ in range(depth):
                e = Abstraction(Application(e, Index(0)))
            if e == old:
                return NEGATIVEINFINITY
            else:
                return self.logLikelihood(tp, e)


class RegisterPrimitives(object):
    def invented(self, e): e.body.visit(self)

    def primitive(self, e):
        if e.name not in Primitive.GLOBALS:
            Primitive(e.name, e.tp, e.value)

    def index(self, e): pass

    def application(self, e):
        e.f.visit(self)
        e.x.visit(self)

    def abstraction(self, e): e.body.visit(self)

    @staticmethod
    def register(e): e.visit(RegisterPrimitives())


class PrettyVisitor(object):
    def __init__(self):
        self.numberOfVariables = 0
        self.freeVariables = {}

        self.variableNames = ["x", "y", "z", "u", "v", "w"]
        self.variableNames += [chr(ord('a') + j)
                               for j in range(20)]
        self.toplevel = True

    def makeVariable(self):
        v = self.variableNames[self.numberOfVariables]
        self.numberOfVariables += 1
        return v

    def invented(self, e, environment, isFunction, isAbstraction):
        s = e.body.visit(self, [], isFunction, isAbstraction)
        return s

    def primitive(
        self,
        e,
        environment,
        isVariable,
        isAbstraction): return e.name

    def index(self, e, environment, isVariable, isAbstraction):
        if e.i < len(environment):
            return environment[e.i]
        else:
            i = e.i - len(environment)
            if i in self.freeVariables:
                return self.freeVariables[i]
            else:
                v = self.makeVariable()
                self.freeVariables[i] = v
                return v

    def application(self, e, environment, isFunction, isAbstraction):
        self.toplevel = False
        s = "%s %s" % (e.f.visit(self, environment, True, False),
                       e.x.visit(self, environment, False, False))
        if isFunction:
            return s
        else:
            return "(" + s + ")"

    def abstraction(self, e, environment, isFunction, isAbstraction):
        toplevel = self.toplevel
        self.toplevel = False
        # Invent a new variable
        v = self.makeVariable()

        body = e.body.visit(self,
                            [v] + environment,
                            False,
                            True)
        if not e.body.isAbstraction:
            body = "." + body
        body = v + body
        if not isAbstraction:
            body = "λ" + body
        if not toplevel:
            body = "(%s)" % body
        return body


def prettyProgram(e):
    return e.visit(PrettyVisitor(), [], True, False)


# from luke
class TokeniseVisitor(object):
    def invented(self, e):
        return [e.body]

    def primitive(self, e): return [e.name]

    def index(self, e):
        return ["$" + str(e.i)]

    def application(self, e):
        return ["("] + e.f.visit(self) + e.x.visit(self) + [")"]

    def abstraction(self, e):
        return ["(_lambda"] + e.body.visit(self) + [")_lambda"]


def tokeniseProgram(e):
    return e.visit(TokeniseVisitor())


def untokeniseProgram(l):
    lookup = {
        "(_lambda": "(lambda",
        ")_lambda": ")"
    }
    s = " ".join(lookup.get(x, x) for x in l)
    return Program.parse(s)
