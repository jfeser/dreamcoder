from ec import explorationCompression, commandlineArguments, Task
from grammar import Grammar
from utilities import eprint, testTrainSplit, numberOfCPUs
from makeTextTasks import makeTasks, delimiters
from textPrimitives import primitives
from program import *
from recognition import *

import random

def stringFeatures(s):
    return [len(s)] + [sum(x == d for x in s ) for d in delimiters ] + [sum(x.upper() == x for x in s )]
def problemFeatures(examples):
    inputFeatures = []
    outputFeatures = []
    for (x,),y in examples:
        inputFeatures.append(stringFeatures(x))
        outputFeatures.append(stringFeatures(y))
    n = float(len(examples))
    inputFeatures = map(lambda *a: sum(a)/n, *inputFeatures)
    outputFeatures = map(lambda *a: sum(a)/n, *outputFeatures)
    return inputFeatures + outputFeatures

class FeatureExtractor(HandCodedFeatureExtractor):
    def _featuresOfProgram(self, program, tp):
        inputs = [ map(fst, t.examples) for t in self.tasks ]
        random.shuffle(inputs)
        ys = None
        for xs in inputs:
            try:
                ys = [ program.runWithArguments(x) for x in xs ]
                eprint(program, xs, ys)
                break
            except: continue
            
        if ys is None: return None
        return problemFeatures(zip(xs,ys))

class LearnedFeatureExtractor(RecurrentFeatureExtractor):
    def __init__(self, tasks):
        lexicon = set([ c
                        for t in tasks
                        for (x,),y in t.examples
                        for xp in [x if isinstance(x,str) else "".join(x)]
                        for yp in [y if isinstance(y,str) else "".join(y)] 
                        for c in xp+yp ] + ["LIST","LISTDELIMITER"])
        def serialize(x):
            if isinstance(x,str): return x
            assert isinstance(x,list)
            serialization = ["LIST"]
            for s in x:
                serialization.append("LISTDELIMITER")
                serialization += [c for c in s ]
            return serialization
            
        def tokenize(examples, _):
            return [ ((serialize(x),), serialize(y))
                     for (x,),y in examples]            
                
        super(LearnedFeatureExtractor, self).__init__(lexicon = list(lexicon),
                                                      H = 16,
                                                      tasks = tasks,
                                                      bidirectional = True,
                                                      tokenize = tokenize)


if __name__ == "__main__":
    tasks = makeTasks()
    for t in tasks:
        t.features = problemFeatures(t.examples)
    eprint("Generated",len(tasks),"tasks")

    test, train = testTrainSplit(tasks, 0.2)

    # target = "Apply double delimited by '<' to input delimited by '>'"
    # program = Program.parse("(lambda (join (chr->str '<') (map (lambda (++ $0 $0)) (split '<' $0))))")
    # eprint(program)
    # tasks = {t.name: t for t in tasks }
    baseGrammar = Grammar.uniform(primitives)
    # eprint(baseGrammar.closedLogLikelihood(tasks[target].request, program))
    # callCompiled(enumerateForTask,
    #              baseGrammar, tasks[target],
    #              maximumFrontier = 2,
    #              timeout = 10000)
    # assert False

    explorationCompression(baseGrammar, train,
                           testingTasks = test,
                           outputPrefix = "experimentOutputs/text",
                           evaluationTimeout = None,
                           **commandlineArguments(
#                               frontierSize = 10**4,
                               steps = 100,
                               iterations = 10,
                               helmholtzRatio = 0.5,
                               topK = 2,
                               maximumFrontier = 1000,
                               structurePenalty = 5.,
                               a = 3,
                               activation = "relu",
                               CPUs = numberOfCPUs(),
                               featureExtractor = LearnedFeatureExtractor,
                               pseudoCounts = 10.0))
