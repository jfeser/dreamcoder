
from utilities import eprint
from recognition import *
from frontier import *
from program import *
from type import *
from task import *
from enumeration import *
from grammar import *
from fragmentGrammar import *

import os

import torch


class ECResult():
    def __init__(self, _=None,
                 learningCurve=None,
                 grammars=None,
                 taskSolutions=None,
                 averageDescriptionLength=None,
                 parameters=None):
        self.averageDescriptionLength = averageDescriptionLength or []
        self.parameters = parameters
        self.learningCurve = learningCurve or []
        self.grammars = grammars or []
        self.taskSolutions = taskSolutions or {}

    def __repr__(self):
        attrs = ["{}={}".format(k, v) for k, v in self.__dict__.items()]
        return "ECResult({})".format(", ".join(attrs))


def explorationCompression(grammar, tasks,
                           _=None,
                           iterations=None,
                           resume=None,
                           frontierSize=None,
                           useRecognitionModel=True,
                           helmholtzRatio = 0.,
                           featureExtractor = None,
                           activation='relu',
                           KLRegularize=0.1,
                           topK=1,
                           maximumFrontier=None,
                           pseudoCounts=1.0, aic=1.0,
                           structurePenalty=0.001, arity=0,
                           CPUs=1,
                           cuda=False,
                           outputPrefix=None):
    if frontierSize is None:
        eprint("Please specify a frontier size:",
               "explorationCompression(..., frontierSize = ...)")
        assert False
    if iterations is None:
        eprint("Please specify a iteration count: explorationCompression(..., iterations = ...)")
        assert False
    if useRecognitionModel and \
            not all(len(t.features) == len(tasks[0].features) for t in tasks):
        eprint("Warning: Recognition model needs features to all have the same dimensionality.",
               "Ignoring recognition model.")
        useRecognitionModel = False
    if helmholtzRatio > 0. and featureExtractor is None:
        eprint("Warning: Using Helmholtz-machine style training requires a feature extractor.",
               "The feature extractor should take a program and a type and return a list of floats.",
               "Setting Helmholtz ratio to 0.")
        helmholtzRatio = 0.        

    # We save the parameters that were passed into EC
    # This is for the purpose of exporting the results of the experiment
    parameters = {k: v for k, v in locals().iteritems()
                  if k not in ["tasks", "grammar", "cuda", "_", "CPUs", "outputPrefix", "resume", "featureExtractor"]}
    if not useRecognitionModel:
        for k in ["KLRegularize","activation","helmholtzRatio"]: del parameters[k]

    eprint("Running EC on %s with parameters:"%(os.uname()[1]))
    for k,v in parameters.iteritems():
        eprint("\t", k, " = ", v)
    eprint()
        

    # Uses `parameters` to construct the checkpoint path
    def checkpointPath(iteration, extra=""):
        parameters["iterations"] = iteration
        kvs = ["{}={}".format(k, parameters[k]) for k in sorted(parameters.keys())]
        return "{}_{}{}.pickle".format(outputPrefix, "_".join(kvs), extra)

    # Restore checkpoint
    if resume is not None:
        path = checkpointPath(resume)
        with open(path, "rb") as handle:
            result = pickle.load(handle)
        eprint("Loaded checkpoint from", path)
        grammar = result.grammars[-1]
    else:  # Start from scratch
        result = ECResult(parameters=parameters, grammars=[grammar],
                          taskSolutions = { t: Frontier([], task = t) for t in tasks })

    for j in range(resume or 0, iterations):
        frontiers = callCompiled(enumerateFrontiers, grammar, frontierSize, tasks,
                                 maximumFrontier=maximumFrontier, CPUs=CPUs)

        eprint("Enumeration results:")
        eprint(Frontier.describe(frontiers))

        tasksHitTopDown = {f.task for f in frontiers if not f.empty}
        if j == 0: # the first iteration is special: it corresponds to the base grammar
            result.learningCurve.append(sum(not f.empty for f in frontiers))
            result.averageDescriptionLength.append(
                -sum(f.bestPosterior.logPosterior for f in frontiers if not f.empty)
                / sum(not f.empty for f in frontiers))

        if useRecognitionModel: # Train and then use a recognition model            
            recognizer = RecognitionModel(len(tasks[0].features), grammar, activation=activation, cuda=cuda)

            # We want to train the recognition model on _every_ task that we have found a solution to
            # `frontiers` only contains solutions from the most recent generative model
            trainingFrontiers = [ f if not f.empty \
                                  else grammar.rescoreFrontier(result.taskSolutions[f.task])
                                  for f in frontiers ]
            
            recognizer.train(trainingFrontiers, KLRegularize=KLRegularize, topK=topK,
                             featureExtractor = featureExtractor,
                             # Disable Helmholtz on the first iteration
                             # Otherwise we just draw from the base grammar which is a terrible distribution
                             helmholtzRatio = helmholtzRatio and helmholtzRatio*int(j > 0))
            bottomupFrontiers = recognizer.enumerateFrontiers(frontierSize, tasks, CPUs=CPUs,
                                                              maximumFrontier = maximumFrontier)
            eprint("Bottom-up enumeration results:")
            eprint(Frontier.describe(bottomupFrontiers))

            tasksHitBottomUp = {f.task for f in bottomupFrontiers if not f.empty}
            showHitMatrix(tasksHitTopDown, tasksHitBottomUp, tasks)

            bottomupHits = sum(not f.empty for f in bottomupFrontiers)
            if j > 0:
                result.averageDescriptionLength.append(
                    -sum(f.bestPosterior.logPosterior for f in bottomupFrontiers if not f.empty)
                    / bottomupHits)

            # Rescore the frontiers according to the generative model
            # and then combine w/ original frontiers
            bottomupFrontiers = [ grammar.rescoreFrontier(f) for f in bottomupFrontiers ]

            frontiers = [f.combine(b) for f, b in zip(frontiers, bottomupFrontiers)]
        elif j > 0:
            result.averageDescriptionLength.append(
                -sum(f.bestPosterior.logPosterior for f in frontiers if not f.empty)
                / sum(not f.empty for f in frontiers))

        

        # Record the new solutions
        result.taskSolutions = {f.task: f.topK(topK) if not f.empty
                                else result.taskSolutions.get(f.task, None)
                                for f in frontiers}

        # The compression procedure is _NOT_ guaranteed to give a
        # grammar that hits all the tasks that were hit in the
        # previous iteration

        # This is by design - the grammar is supposed to _generalize_
        # based on what it has seen, and so it will necessarily
        # sometimes put less probability mass on programs that it has
        # seen. If you put pseudocounts = 0, THEN you will get a
        # non-decreasing number of hit tasks.

        # So, if we missed a task that was previously hit, then we
        # should go back and add back in that solution
        if False:
            for i, f in enumerate(frontiers):
                if not f.empty or result.taskSolutions[f.task] is None:
                    continue
                frontiers[i] = result.taskSolutions[f.task]

        # number of hit tasks
        if j > 0:
            # The first iteration is special: what we record is the
            # performance of the base grammar without any learning
            result.learningCurve.append(sum(not f.empty for f in frontiers))

        grammar = callCompiled(induceFragmentGrammarFromFrontiers,
                               grammar,
                               frontiers,
                               topK=topK,
                               pseudoCounts=pseudoCounts,
                               aic=aic,
                               structurePenalty=structurePenalty,
                               a=arity,
                               CPUs=CPUs
                               # ,profile = "profiles/grammarInduction"
        ).toGrammar()
        result.grammars.append(grammar)
        eprint("Grammar after iteration %d:" % (j + 1))
        eprint(grammar)

        if outputPrefix is not None:
            path = checkpointPath(j + 1)
            with open(path, "wb") as handle:
                pickle.dump(result, handle)
            eprint("Exported checkpoint to", path)

    return result

def showHitMatrix(top, bottom, tasks):
    tasks = set(tasks)
    
    total = bottom|top
    eprint(len(total),"/",len(tasks),"total hit tasks")
    bottomMiss = tasks - bottom
    topMiss = tasks - top
    
    eprint("{: <13s}{: ^13s}{: ^13s}".format("","bottom miss","bottom hit"))
    eprint("{: <13s}{: ^13d}{: ^13d}".format("top miss",
                                             len(bottomMiss & topMiss),
                                             len(bottom & topMiss)))
    eprint("{: <13s}{: ^13d}{: ^13d}".format("top hit",
                                             len(top & bottomMiss),
                                             len(top & bottom)))

def commandlineArguments(_=None,
                         iterations=None,
                         frontierSize=None,
                         topK=1,
                         CPUs=1,
                         useRecognitionModel=True,
                         activation='relu',
                         helmholtzRatio = 0.,
                         featureExtractor = None,
                         cuda=None,
                         maximumFrontier=None,
                         pseudoCounts=1.0, aic=1.0,
                         structurePenalty=0.001, a=0,
                         KLRegularize=None):
    if cuda is None:
        cuda = torch.cuda.is_available()
    import argparse
    parser = argparse.ArgumentParser(description="")
    parser.add_argument("--resume",
                        help="Resumes EC algorithm from checkpoint",
                        default=None,
                        type=int)
    parser.add_argument("-i", "--iterations",
                        help="default: %d" % iterations,
                        default=iterations,
                        type=int)
    parser.add_argument("-f", "--frontierSize",
                        default=frontierSize,
                        help="default: %d" % frontierSize,
                        type=int)
    parser.add_argument("-k", "--topK",
                        default=topK,
                        help="When training generative and discriminative models, we train them to fit the top K programs. Ideally we would train them to fit the entire frontier, but this is often intractable. default: %d" % topK,
                        type=int)
    parser.add_argument("-p", "--pseudoCounts",
                        default=pseudoCounts,
                        help="default: %f" % pseudoCounts,
                        type=float)
    parser.add_argument("-b", "--aic",
                        default=aic,
                        help="default: %f" % aic,
                        type=float)
    parser.add_argument("-l", "--structurePenalty",
                        default=structurePenalty,
                        help="default: %f" % structurePenalty,
                        type=float)
    parser.add_argument("-a", "--arity",
                        default=a,
                        help="default: %d" % a,
                        type=int)
    parser.add_argument("-c", "--CPUs",
                        default=CPUs,
                        help="default: %d" % CPUs,
                        type=int)
    parser.add_argument("--no-cuda",
                        action="store_false",
                        dest="cuda",
                        help="""cuda will be used if available (which it %s),
                        unless this is set""" % ("IS" if cuda else "ISN'T"))
    parser.add_argument("-m", "--maximumFrontier",
                        help="""Even though we enumerate --frontierSize
                        programs, we might want to only keep around the very
                        best for performance reasons. This is a cut off on the
                        maximum size of the frontier that is kept around.
                        Default: %s""" % maximumFrontier,
                        type=int)
    parser.add_argument("--recognition",
                        dest="useRecognitionModel",
                        action="store_true",
                        help="""Enable bottom-up neural recognition model.
                        Default: %s""" % useRecognitionModel)
    parser.add_argument("-g", "--no-recognition",
                        dest="useRecognitionModel",
                        action="store_false",
                        help="""Disable bottom-up neural recognition model.
                        Default: %s""" % (not useRecognitionModel))
    parser.add_argument("--activation",
                        choices=["relu", "sigmoid", "tanh"],
                        default=activation,
                        help="""Activation function for neural recognition model.
                        Default: %s""" % activation)
    parser.add_argument("--kl-factor", metavar="FACTOR",
                        dest="KLRegularize",
                        help="""Regularization factor for KL divergence against
                        induced grammar for neural recognition model.
                        Default: %s""" % KLRegularize,
                        type=float)
    parser.add_argument("-r","--Helmholtz",
                        dest="helmholtzRatio",
                        help="""When training recognition models, what fraction of the training data should be samples from the generative model? Default %f""" % helmholtzRatio,
                        type=float)
    parser.set_defaults(useRecognitionModel=useRecognitionModel,
                        KLRegularize=KLRegularize,
                        featureExtractor = featureExtractor,
                        cuda=cuda)
    v = vars(parser.parse_args())
    #v.featureExtractor = featureExtractor
    return v
