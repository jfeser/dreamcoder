from utilities import eprint
from frontier import *
from task import *
from type import *
from program import *
from grammar import *

import gc

def enumerateFrontiers(g, frontierSize, tasks, CPUs=1, maximumFrontier=None, verbose=True):
    '''g: Either a Grammar, or a map from task to grammar.'''
    from time import time

    if isinstance(g, dict):
        start = time()
        f = parallelMap(CPUs,            
                        lambda (task, grammar): enumerateFrontiers(grammar, frontierSize, [task],
                                                                   CPUs=1, verbose=False,
                                                                   maximumFrontier=maximumFrontier)[0],
                        map(lambda t: (t, g[t]), tasks))
        if verbose:
            eprint("Enumerated %d frontiers in time %f"%(len(g), time() - start))
        return f

    start = time()
    uniqueRequests = list({ t.request for t in tasks })
    frontiers = dict(parallelMap(
        CPUs,
        lambda request: (request, iterativeDeepeningEnumeration(g, request, frontierSize,
                                                                showDescriptionLength = verbose)),
        uniqueRequests))
    totalNumberOfPrograms = sum(len(f) for f in frontiers.values())
    totalNumberOfFrontiers = len(frontiers)

    frontiers = {t: frontiers[t.request] for t in tasks}

    if verbose:
        eprint("Enumerated %d frontiers with %d total programs in time %fsec" %
               (totalNumberOfFrontiers, totalNumberOfPrograms, time() - start))

    # I think that this is no longer needed...
    # # In general these programs have considerable overlap, reusing
    # # many subtrees. This code will force identical trees to only be
    # # represented by a single object on the heap.
    # if isinstance(g, dict):
    #     with timing("Coalesced trees"):
    #         share = ShareVisitor()
    #         frontiers = {t: [ (l, share.execute(p)) for l,p in f ]
    #                      for t,f in frontiers.iteritems() }
    #         share = None # collect it
    #         gc.collect()

    start = time()
    # We split up the likelihood calculation and the frontier construction
    # This is so we do not have to serialize and deserialize a bunch of programs
    # programLikelihoods: [ {indexInfrontiers[task]: likelihood} (for each task)]
    programLikelihoods = parallelMap(CPUs, lambda task:
                                     {j: logLikelihood
                                      for j, (_, program) in enumerate(frontiers[task])
                                      for logLikelihood in [task.logLikelihood(program)]
                                      if valid(logLikelihood)},
                                     tasks)

    frontiers = constructFrontiers(frontiers, programLikelihoods, tasks, maximumFrontier)

    dt = time() - start
    if verbose:
        eprint("Scored frontiers in time %fsec (%f/program)" % (dt, dt / totalNumberOfPrograms))

    return frontiers


def iterativeDeepeningEnumeration(g, request, frontierSize, budget=2.0, budgetIncrement=1.0, showDescriptionLength = False):
    """Returns a list of (log likelihood, program)"""
    frontier = []
    while len(frontier) < frontierSize:
        frontier = [(l, p) for l, _, p in enumeration(g, Context.EMPTY, [], request, budget)]
        budget += budgetIncrement
    if showDescriptionLength: eprint("Enumerated up to %f nats"%(budget - budgetIncrement))
    # This will trim the frontier to be exactly frontierSize We do
    # this for small frontier sizes; the idea is that if the frontier
    # is small then you probably want exactly that many programs
    if frontierSize <= 2000: return sorted(frontier, key=lambda (l, p): l, reverse=True)[:frontierSize]
    return frontier


def enumeration(g, context, environment, request, budget, maximumDepth = 20):
    if budget <= 0 or maximumDepth == 1:
        return
    if request.isArrow():
        v = request.arguments[0]
        for l, newContext, b in enumeration(g, context, [v] + environment,
                                            request.arguments[1], budget,
                                            maximumDepth = maximumDepth):
            yield l, newContext, Abstraction(b)

    else:
        candidates = g.buildCandidates(request, context, environment,
                                       normalize = True)
        
        for l, t, p, newContext in candidates:
            xs = t.functionArguments()
            for result in enumerateApplication(g, newContext, environment, p, l, xs, budget + l,
                                               maximumDepth = maximumDepth - 1):
                yield result


def enumerateApplication(g, context, environment,
                         function, functionLikelihood, argumentRequests, budget,
                         maximumDepth = 20):
    if argumentRequests == []:
        yield functionLikelihood, context, function
    else:
        argRequest = argumentRequests[0].apply(context)
        laterRequests = argumentRequests[1:]
        for argL, newContext, arg in enumeration(g, context, environment, argRequest, budget,
                                                 maximumDepth = maximumDepth):
            newFunction = Application(function, arg)
            for result in enumerateApplication(g, newContext, environment, newFunction,
                                               functionLikelihood + argL,
                                               laterRequests, budget + argL,
                                               maximumDepth = maximumDepth):
                yield result


def constructFrontiers(frontiers, programLikelihoods, tasks, maxFrontier):
    newFrontiers = []
    for programLikelihood, task in zip(programLikelihoods, tasks):
        entries = []
        for j, (logPrior, program) in enumerate(frontiers[task]):
            ll = programLikelihood.get(j, NEGATIVEINFINITY)
            entry = FrontierEntry(program, logPrior=logPrior, logLikelihood=ll)
            entries.append(entry)
        frontier = Frontier(entries, task=task).removeZeroLikelihood()
        newFrontiers.append(frontier.topK(maxFrontier))
    return newFrontiers
