"""
Defines featurizers and parsers for conditioning on language.
"""
from dreamcoder.enumeration import *
from dreamcoder.grammar import *
from dreamcoder.recognition import *
from dreamcoder.utilities import *

import torch
import torch.nn as nn
import torch.nn.functional as F

import nltk
import num2words

from sklearn.feature_extraction import DictVectorizer

class TokenRecurrentFeatureExtractor(RecurrentFeatureExtractor):
    """
    GRU Feature extractor over pre-tokenized language data.
    """
    def __init__(self, tasks, testingTasks, cuda, language_data,
                canonicalize_numbers=True, tokenizer_fn=None,
                H=64,
                bidirectional=True,
                max_inputs=5):
        self.canonicalize_numbers = canonicalize_numbers
        self.tokenizer_fn = tokenizer_fn
        if self.tokenizer_fn is None:
            from nltk.tokenize import word_tokenize
            self.tokenizer_fn = word_tokenize
        self.language_data = language_data
        self.tokenized_tasks = dict()
        self.useTask = True
        self.MAXINPUTS = max_inputs
        super(TokenRecurrentFeatureExtractor, self).__init__(lexicon=self.build_lexicon(),
                                                             H=H,
                                                             tasks=tasks,
                                                             bidirectional=True,
                                                             cuda=cuda)
        self.trained = True

    def canonicalize_number_token(self, t):
        from num2words import num2words
        try:
            t = num2words(int(t), to='cardinal')
        except:
            pass
        return t
    
    def tokenize(self, task):
        if task in self.tokenized_tasks:
            return self.tokenized_tasks[task]
        if task not in self.language_data:
            return None
        
        tokens = []
        for sentence in self.language_data[task]:
            sentence_tokens = self.tokenizer_fn(sentence)
            if self.canonicalize_numbers:
                sentence_tokens = [self.canonicalize_number_token(t) for t in sentence_tokens]
            tokens.append(sentence_tokens) # Feature extractor examples are usually inputs and outputs
        self.tokenized_tasks[task] = [ 
                                        [tokens, []]
                                     ]
        return self.tokenized_tasks[task] # Feature extractor examples are usually lists of (xs, y) sets.

                
    def build_lexicon(self):
        lexicon = set()
        for task in self.language_data:
            tokens = self.tokenize(task)[0][0]
            for sentence_tokens in tokens:
                lexicon.update(set(sentence_tokens))
        eprint("Built a lexicon of {} words".format(len(lexicon)))
        return list(lexicon)
    
class NgramFeaturizer(nn.Module):
    """
    Tokenizes and extracts n-gram and skip-gram features from text.
    
    n: maximum length of any n-grams, including skips.
    skip_n: maximum skip distance, which will be calculated on bigrams only.
    Default is: unigrams, bigrams, trigrams, skip-trigrams.
    """
    def __init__(self, tasks, testingTasks, cuda, language_data,
    max_n=3, skip_n=1, canonicalize_numbers=True, tokenizer_fn=None):
        super(NgramFeaturizer, self).__init__()
        self.trained = False
        
        self.max_n = max_n
        self.skip_n = skip_n
        self.canonicalize_numbers = canonicalize_numbers
        self.tokenizer_fn = tokenizer_fn
        if self.tokenizer_fn is None:
            from nltk.tokenize import word_tokenize
            self.tokenizer_fn = word_tokenize
        self.language_dict_vectorizer = None
        
        # Initialize the vectorizer. Fits to all task n-grams.
        self.language_data = language_data
        self.sorted_tasks = sorted(self.language_data.keys(), key=lambda t:t.name)
        self.fit_language_features(self.sorted_tasks, self.language_data)
        
        if cuda: self.cuda()
    
    def canonicalize_number_token(self, t):
        from num2words import num2words
        try:
            t = num2words(int(t), to='cardinal')
        except:
            pass
        return t 
    
    def ngrams_skipgrams(self, tokens):
        features = list()
        
        for n in range(1, self.max_n+1):
            ngram_tokens = tokens
            for i in range(len(ngram_tokens) - (n-1)):
                ngram = tuple(ngram_tokens[i:i+n])
                features.append(ngram)
            if n==2:
                for i in range(len(tokens) - (n+1)):
                    skip = tuple([tokens[i]]+['*']*self.skip_n+[tokens[i+self.skip_n+1]])
                    features.append(skip)
            
        return features
    
    def featurize(self, sentence):
        """:ret: Counter containing ngram and skipgram features."""
        from collections import Counter
        tokens = self.tokenizer_fn(sentence)
        if self.canonicalize_numbers:
            tokens = [self.canonicalize_number_token(t) for t in tokens]
        features = Counter(self.ngrams_skipgrams(tokens))
        return features
    
    def featurize_sentences(self, sentences):
        """:ret: set containing ngram and skipgram features."""
        from collections import Counter
        features = Counter()
        for s in sentences:
            features += self.featurize(s) 
        return features
    
    def fit_language_features(self, sorted_keys, language_data):
        """
        Featurizes language_data dict from { sorted keys : sentences }
        Sets self.language_dict_vectorizer; self.featurized_language_data
        :ret: 
            language_dict_vectorizer: dict vectorizer over language_data
            featurized_language_data: {key : torch feature vector}
        """
        key_features = [self.featurize_sentences(language_data[t]) for t in sorted_keys]
        self.language_dict_vectorizer = DictVectorizer(sparse=False)
        vectorized_features = self.language_dict_vectorizer.fit_transform(key_features)
        
        self.featurized_language_data = {
            k : variable(torch.from_numpy(vectorized_features[i])).float()
            for (i, k) in enumerate(sorted_keys)
        }
        
        n_features = len(self.language_dict_vectorizer.get_feature_names())
        eprint("Fitting language data with n-gram features: {} features from {} tasks".format(n_features, len(language_data)))
        
        self.trained = True
        return self.language_dict_vectorizer, self.featurized_language_data
        
    @property
    def outputDimensionality(self): 
        """Required to work with recognition model"""
        return len(self.language_dict_vectorizer.get_feature_names())
    
    def featuresOfTask(self, t):
        """Required to work with recognition model"""
        assert self.language_dict_vectorizer is not None
        if t not in self.featurized_language_data: return None
        features = self.featurized_language_data[t]
        if self.cuda:
            features = features.cuda()
        return features

        
class LogLinearBigramTransitionParser(nn.Module):
    """
    Log-linear tree-bigram transition model.
    
    Predicts P(primitive | bigram, sentence for each bigram), in the style of the existing ContextualGrammarNetwork.
    During enumeration, produces a full ContextualGrammar that can be used directly for enumeration.
    """
    
    def __init__(self, grammar, 
                language_data,
                frontiers,
                language_feature_extractor,
                tasks,
                testingTasks,
                cuda=False):
        super(LogLinearBigramTransitionParser, self).__init__()        
        self.use_cuda = cuda
        self.language_data = language_data
        
        if language_feature_extractor is None:
            language_feature_extractor = NgramFeaturizer
        self.language_featurizer = language_feature_extractor(tasks, testingTasks, cuda, language_data)

        self.unigram_grammar = grammar
        self.bigram_grammar = ContextualGrammar.fromGrammar(grammar)
        
        self.frontiers = frontiers
        self.frontier_likelihoods = None
        # Linear network: inputs are Bxn_features vector; outputs are a Bxn_bigrams dimensional vector.
        # Theta = n_features x n_bigrams parameters.
        self.log_linear_bigram_network = ContextualGrammarNetwork(inputDimensionality=self.language_featurizer.outputDimensionality, grammar=self.unigram_grammar)
        
        if self.use_cuda: self.cuda()
                
    def fit_program_features(self):
        """
        Precalculates bigram likelihood summaries for tasks where we have language data.
        """
        def replaceProgramsWithBigramLikelihoodSummaries(frontier):
            return Frontier(
                [FrontierEntry(
                    program=self.bigram_grammar.closedLikelihoodSummary(frontier.task.request, e.program),
                    logLikelihood=e.logLikelihood,
                    logPrior=e.logPrior) for e in frontier],
                task=frontier.task)
        
        self.frontier_likelihoods = {
            task : replaceProgramsWithBigramLikelihoodSummaries(f).normalize()
            for (task, f) in self.frontiers.items() if not f.empty
        }
    
    def loss_bias_optimal(self, frontier_likelihood):
        batch_size = len(frontier_likelihood.entries)
        language_features = self.language_featurizer.featuresOfTask(frontier_likelihood.task)
        if language_features is None:
            return None

        language_features.detach()
        language_features = language_features.expand(batch_size, language_features.size(-1))
        
        lls = self.log_linear_bigram_network.batchedLogLikelihoods(language_features, [entry.program for entry in frontier_likelihood])
        actual_ll = torch.Tensor([ entry.logLikelihood for entry in frontier_likelihood])
        lls = lls + (actual_ll.cuda() if self.use_cuda else actual_ll)
        ml = -lls.max()
        return ml
        
    def train_epoch(self):
        assert self.frontier_likelihoods is not None
        assert self.language_featurizer.trained == True
        start = time.time()
        epoch_losses = []
        self.zero_grad()
        
        shuffled_tasks = list(self.frontier_likelihoods.keys())
        random.shuffle(shuffled_tasks)
        for task in shuffled_tasks:
            frontier_likelihood = self.frontier_likelihoods[task] 
            ll_loss = self.loss_bias_optimal(frontier_likelihood)
            loss = ll_loss
            loss.backward()
            self.optimizer.step()
            epoch_losses.append(ll_loss.data.item())
        gc.collect()
        epoch_time = time.time() - start
        return mean(epoch_losses), epoch_time

    def train(self, epochs=None, lr=0.001):
        self.fit_program_features() # Precalculate the likelihood outputs.
        
        eprint("Fitting parser model: {}".format(self.__class__.__name__))
        eprint("Fitting to {} frontiers with language data.".format(len(self.frontier_likelihoods)))
        eprint("Fitting for {} epochs.".format(epochs))
        self.optimizer = torch.optim.SGD(self.parameters(), lr=lr)
        
        for epoch_i in range(1, epochs + 1):
            epoch_loss, time = self.train_epoch()
            eprint("Epoch: {}: Loss: {}, t = {} secs".format(epoch_i, epoch_loss, time))
        
        return self
    
    def contextual_grammar_for_task(self, task):
        language_features = self.language_featurizer.featuresOfTask(task)
        if language_features is None:
            return None
    
        return self.log_linear_bigram_network(language_features)
        
    def enumerateFrontiers(self, 
                  tasks=None,
                  testing=False,
                  enumerationTimeout=None,
                  solver=None,
                  CPUs=1,
                  maximumFrontier=None, 
                  evaluationTimeout=None):
        """:ret: bigram grammar for contextual enumeration."""
        
        with timing("Evaluated language-based transition model"):
            grammars = {task: self.contextual_grammar_for_task(task)
                        for task in tasks}
            #untorch seperately to make sure you filter out None grammars
            grammars = {task: grammar.untorch() for task, grammar in grammars.items() if grammar is not None}

        return multicoreEnumeration(grammars, tasks,
                                    testing=testing,
                                    solver=solver,
                                    enumerationTimeout=enumerationTimeout,
                                    CPUs=CPUs, maximumFrontier=maximumFrontier,
                                    evaluationTimeout=evaluationTimeout)