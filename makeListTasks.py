from __future__ import division

from type import *
from task import RegressionTask
from utilities import eprint

from random import randint

import listroutines as lr

# Excluded routines either impossible or astronomically improbable
# I'm cutting these off at ~20 nats in learned grammars.
EXCLUDES = {
    "dedup",
    "intersperse-k",
    "pow-base-k",
    "replace-all-k-with-n",
    "replace-index-k-with-n",
    "uniq",
}


def list_features(examples):
    if any(isinstance(i, int) for (i,), _ in examples):
        # obtain features for number inputs as list of numbers
        examples = [(([i],), o) for (i,), o in examples]
    elif any(not isinstance(i, list) for (i,), _ in examples):
        # can't handle non-lists
        return []
    elif any(isinstance(x, list) for (xs,), _ in examples for x in xs):
        # nested lists are hard to extract features for, so we'll
        # obtain features as if flattened
        examples = [(([x for xs in ys for x in xs],), o) for (ys,), o in examples]

    # assume all tasks have the same number of examples
    # and all inputs are lists
    features = []
    ot = type(examples[0][1])
    mean = lambda l: 0 if not l else sum(l)/len(l)
    imean = [mean(i) for (i,), o in examples]
    ivar = [sum((v - imean[idx])**2
                for v in examples[idx][0][0])
            for idx in xrange(len(examples))]

    #DISABLED length of each input and output
    # total difference between length of input and output
    #DISABLED normalized count of numbers in input but not in output
    # total normalized count of numbers in input but not in output
    # total difference between means of input and output
    # total difference between variances of input and output
    # output type (-1=bool, 0=int, 1=list)
    #DISABLED outputs if integers, else -1s
    #DISABLED outputs if bools (-1/1), else 0s
    if ot == list:  # lists of ints or bools
        omean = [mean(o) for (i,), o in examples]
        ovar = [sum((v - omean[idx])**2
                    for v in examples[idx][1])
                for idx in xrange(len(examples))]
        cntr = lambda l, o: 0 if not l else len(set(l).difference(set(o))) / len(l)
        cnt_not_in_output = [cntr(i, o) for (i,), o in examples]

        #features += [len(i) for (i,), o in examples]
        #features += [len(o) for (i,), o in examples]
        features.append(sum(len(i) - len(o) for (i,), o in examples))
        #features += cnt_not_int_output
        features.append(sum(cnt_not_in_output))
        features.append(sum(om - im for im, om in zip(imean, omean)))
        features.append(sum(ov - iv for iv, ov in zip(ivar, ovar)))
        features.append(1)
        # features += [-1 for _ in examples]
        # features += [0 for _ in examples]
    elif ot == bool:
        outs = [o for (i,), o in examples]

        #features += [len(i) for (i,), o in examples]
        #features += [-1 for _ in examples]
        features.append(sum(len(i) for (i,), o in examples))
        #features += [0 for _ in examples]
        features.append(0)
        features.append(sum(imean))
        features.append(sum(ivar))
        features.append(-1)
        # features += [-1 for _ in examples]
        # features += [1 if o else -1 for o in outs]
    else:  # int
        cntr = lambda l, o: 0 if not l else len(set(l).difference(set(o))) / len(l)
        cnt_not_in_output = [cntr(i, [o]) for (i,), o in examples]
        outs = [o for (i,), o in examples]

        #features += [len(i) for (i,), o in examples]
        #features += [1 for (i,), o in examples]
        features.append(sum(len(i) for (i,), o in examples))
        #features += cnt_not_int_output
        features.append(sum(cnt_not_in_output))
        features.append(sum(o - im for im, o in zip(imean, outs)))
        features.append(sum(ivar))
        features.append(0)
        # features += outs
        # features += [0 for _ in examples]

    return features


def make_list_task(name, examples, **params):
    input_type = guess_type([i for (i,), _ in examples])
    output_type = guess_type([o for _, o in examples])

    # We can internally handle lists of bools.
    # We explicitly create these by modifying existing routines.
    if name.startswith("identify"):
        boolexamples = [((i,), map(bool, o)) for (i,), o in examples]
        for t in make_list_task("bool-"+name, boolexamples, **params):
            yield t
        # for now, we'll stick with the boolean-only tasks and not have a copy
        # for integers.
        return

    program_type = arrow(input_type, output_type)
    features = list_features(examples)
    cache = all(hashable(x) for x in examples)

    if params:
        eq_params = ["{}={}".format(k, v) for k, v in params.items()]
        if len(eq_params) == 1:
            ext = eq_params[0]
        elif len(eq_params) == 2:
            ext = "{} and {}".format(*eq_params)
        else:
            ext = ", ".join(eq_params[:-1])
            ext = "{}, and {}".format(ext, eq_params[-1])
        name += " with " + ext

    yield RegressionTask(name, program_type, examples, features=features, cache=cache)


def make_list_tasks(n_examples):
    for routine in lr.find(count=100):  # all routines
        if routine.id in EXCLUDES:
            continue
        if routine.is_parametric():
            for params in routine.example_params():
                bigs = [k for k, v in params.items()
                        if type(v) == int and abs(v) > 9]
                for k in bigs:  # reduce big constants
                    params[k] = randint(1, 9)
                if routine.id == "rotate-k":
                    # rotate-k is hard if list is smaller than k
                    k = params["k"]
                    if k < 1:
                        continue
                    inps = []
                    for _ in xrange(n_examples):
                        r = randint(abs(k) + 1, 17)
                        inp = routine.gen(len=r, **params)[0]
                        inps.append(inp)
                else:
                    inps = routine.gen(count=n_examples, **params)
                examples = [((inp,), routine.eval(inp, **params))
                            for inp in inps]
                for t in make_list_task(routine.id, examples, **params):
                    yield t
        else:
            inps = routine.examples()
            if len(inps) > n_examples:
                inps = inps[:n_examples]
            elif len(inps) < n_examples:
                inps += routine.gen(count=(n_examples - len(inps)))
            examples = [((inp,), routine.eval(inp)) for inp in inps]
            for t in make_list_task(routine.id, examples):
                yield t

N_EXAMPLES=15

def main():
    import sys
    import cPickle as pickle

    n_examples = N_EXAMPLES
    if len(sys.argv) > 1:
        n_examples = int(sys.argv[1])

    eprint("Downloading and generating dataset")
    tasks = sorted(make_list_tasks(n_examples), key=lambda t:t.name)
    eprint("Got {} list tasks".format(len(tasks)))

    with open("data/list_tasks.pkl", "w") as f:
        pickle.dump(tasks, f)
    eprint("Wrote list tasks to data/list_tasks.pkl")


if __name__ == "__main__":
    main()
