# imports
# packages
from flask import Flask, request, jsonify
import sys
from collections import Counter, OrderedDict, defaultdict
import itertools
from itertools import islice, count, groupby
import pandas as pd
import os
import re
from operator import itemgetter
import nltk
from nltk.stem.porter import *
from nltk.corpus import stopwords
from time import time
from timeit import timeit
from pathlib import Path
import pickle
import pandas as pd
import numpy as np
from google.cloud import storage
import csv
import math
from contextlib import closing
import hashlib
import heapq
# other files
from inverted_index_colab import *
from inverted_index_gcp import *


def _hash(s):
    return hashlib.blake2b(bytes(s, encoding='utf8'), digest_size=5).hexdigest()

nltk.download('stopwords')
english_stopwords = frozenset(stopwords.words('english'))
corpus_stopwords = ["category", "references", "also", "external", "links",
                    "may", "first", "see", "history", "people", "one", "two",
                    "part", "thumb", "including", "second", "following",
                    "many", "however", "would", "became"]
all_stopwords = english_stopwords.union(corpus_stopwords)

RE_WORD = re.compile(r"""[\#\@\w](['\-]?\w){2,24}""", re.UNICODE)
stopwords_frozen = frozenset(stopwords.words('english'))

def build_token(query):
    if type(query) != str:
        tokens = query
    else:
        tokens = [token.group() for token in RE_WORD.finditer(query.lower())]
        wstops = tokens
        tokens = []
        for token in wstops:
            if token not in all_stopwords:
                tokens.append(token)
    return tokens

def get_tup_lst(index, tokens):
    tuples = {}
    for token in tokens:
        postings_list = index.read_posting_list_dict(token)
        for doc_id in postings_list:
            sim = postings_list.get(doc_id, 0)
            tuples[doc_id] = sim + 1
    tuples_lst = tuples.items()
    tuples_lst_sorted = sorted(tuples_lst, key=lambda x: x[1], reverse=True)
    return tuples_lst_sorted


def search(title_index, body_index, anchor_index, page_rank, page_views, query):
    tokens = []
    temp_tokens = [token.group() for token in RE_WORD.finditer(query.lower())]
    for token in temp_tokens:
        if (token in all_stopwords) or (token in tokens):
            continue
        tokens.append(token)
    # bin_anchor, _ = search_anchor(anchor_index, tokens)
    # print('search anchor: ', bin_anchor)
    bin_title, _ = search_title(title_index, tokens)
    # print('search title: ', bin_title)
    if len(tokens) <= 1:
        bodies, _ = search_body(body_index, tokens, 5)
        # print('search body: ', bodies)
        bin_body = binary_body(body_index, tokens)
        # print('binary body: ', bin_body)
        scores = []
        for doc_id in bin_body:
            # anchor_score = (bin_anchor.get(doc_id, 0) + 1) ** 3
            # print('anchor score for doc_id=: ', doc_id, anchor_score)
            title_score = (bin_title.get(doc_id, 0) + 1) ** 10
            # print('title score for doc_id=: ', doc_id, title_score)
            # print('Total score: ', bodies[doc_id] * page_rank.get(doc_id, 0) * page_views.get(doc_id,0) * anchor_score * title_score / 1000000)
            # scores.append([doc_id, bodies[doc_id] * page_rank.get(doc_id, 0) * page_views.get(doc_id,0) * anchor_score * title_score / 1000000])
            scores.append([doc_id, bodies[doc_id] * page_rank.get(doc_id, 0) * title_score / 10000])
        sort = heapq.nlargest(100, scores, key=lambda x: x[1])
    else:
        doc_id_bm25_score = {}
        bodies = BM25_from_index(body_index)
        titles = BM25_from_index(title_index)
        sort = bodies.search(tokens, 100)
        scoredTitle = titles.search(tokens, 100)
        for score in sort:
            doc_id_bm25_score[score[0]] = score[1]
        for score in scoredTitle:
            doc_id_bm25_score[score[0]] = doc_id_bm25_score.get(score[0], 0) + score[1]
        scored_title_body = doc_id_bm25_score.items()
        sort = heapq.nlargest(100, scored_title_body, key=lambda x: x[1])
    scored_id = []
    for i in sort:
        scored_id.append(i[0])
    return scored_id

def search_body(index, query, over_tfidf = 1):
    tokens = build_token(query)
    topNdict, topNlist = get_topN_score_for_queries(tokens, index, over_tfidf, N=-1)
    return topNdict, topNlist

def search_title(index, query):
    tokens = build_token(query)
    tuples_lst_sorted = get_tup_lst(index, tokens)
    res_list = [t[0] for t in tuples_lst_sorted]
    res_dict = {}
    for i in tuples_lst_sorted:
        res_dict[i[0]] = i[1]
    return res_dict, res_list

def search_anchor(index, query):
    tokens = build_token(query)
    tuples_lst_sorted = get_tup_lst(index, tokens)
    res_list = [t[0] for t in tuples_lst_sorted]
    res_dict = {}
    for item in tuples_lst_sorted:
        res_dict[item[0]] = item[1]
    return res_dict, res_list


def binary_body(index, query):
    tokens = build_token(query)
    tuples = {}
    # len_real_query = 0
    len_real_query = len(tokens)
    for token in tokens:
        postings_list = index.read_posting_list_dict(token)
        # len_real_query += 1
        for doc_id in postings_list:
            sim = tuples.get(doc_id, 0)
            tuples[doc_id] = sim + 1
    res_sorted = sorted(tuples.items(), key=lambda x: x[1], reverse=True)
    res = {}
    docs = []
    for t in res_sorted:
        res[t[0]] = max(t[1] - len_real_query + 1, 0)
        if res[t[0]] == 1:
            docs.append(t[0])
    if len(docs) < 20:
        docs = [r[0] for r in res_sorted]
    return docs

def get_postings_of_query(index, tokens):
    postings_list = []
    for token in tokens:
        postings_list.append(index.read_posting_list_dict(token))
    return postings_list

def similarity_method(index, query, pls, over_tfidf = 1):
    similarity_dict = {}
    count = 0
    for pl in pls:
        for doc_id in pl:
            tfidf = tfidf_score(index, query[count], pl[doc_id] ** over_tfidf, doc_id, len(index.DL))
            similarity_dict[doc_id] = similarity_dict.get(doc_id, 0) + tfidf
        count += 1
    for doc_id in similarity_dict:
        similarity_dict[doc_id] = similarity_dict[doc_id] / (len(query) ** 0.5 * (index.DL[doc_id] ** 0.5))
    return similarity_dict

def tfidf_score(index, token, tf, doc_id, N):
    ntf = (tf) / index.DL[doc_id]
    idf = math.log(N / index.df[token], 10)
    return ntf * idf

def get_relevance_docs_from_pls(pls):
    docs = {}
    for pl in pls:
        for doc_id_tf in pl:
            docs[doc_id_tf] = doc_id_tf
    return docs

def get_top_n(sim_dict,N=-1): #taken from HW4
    if N == -1:
        N = len(sim_dict)
    res = []
    for doc_id, score in list(sim_dict.items()):
        res.append((doc_id, score))
    return heapq.nlargest(N, res, key=lambda x: x[1])


def get_topN_score_for_queries(tokens, index, above_tfidf = 1, N=-1):
    sorted_sim_dict = {}
    for item in get_top_n(similarity_method(index, tokens, get_postings_of_query(index, tokens), above_tfidf), -1):
        sorted_sim_dict[item[0]] = item[1]
    return sorted_sim_dict, get_top_n(similarity_method(index, tokens, get_postings_of_query(index, tokens), above_tfidf), -1)

def train_ir(train_set):
    train_tokens = {}
    for query in train_set:
        tokens = [token.group() for token in RE_WORD.finditer(query.lower())]
        not_stopwords = ''
        for token in tokens:
            if token not in all_stopwords:
                not_stopwords += token
        train_tokens[not_stopwords] = query
    return train_tokens

# def map40(query, docs):
#     train_tokens = train_ir(train_set)
#     correct = 0
#     query_str = ''
#     for token in query:
#         if token in all_stopwords:
#             continue
#         query_str += token
#     if train_tokens.get(query_str, 0) == 0:
#         return train_tokens, query_str
#     docs_list = train_set[train_tokens.get(query_str, 0)]
#     for doc in docs[:40]:
#         if doc in docs_list:
#             correct += 1
#     return correct/40, query_str, docs_list




# taken from HW4 - from this line until the end
class BM25_from_index:
    """
    Best Match 25.
    ----------
    k1 : float, default 1.5

    b : float, default 0.75

    index: inverted index
    """

    def __init__(self, index, k1=1.5, b=0.75):
        self.b = b
        self.k1 = k1
        self.index = index
        self.N = len(index.DL)
        self.AVGDL = sum(index.DL.values()) / self.N
        #self.words, self.pls = zip(*self.index.posting_lists_iter())
        self.words, self.pls = [], []

    def calc_idf(self, list_of_tokens):
        """
        This function calculate the idf values according to the BM25 idf formula for each term in the query.

        Parameters:
        -----------
        query: list of token representing the query. For example: ['look', 'blue', 'sky']

        Returns:
        -----------
        idf: dictionary of idf scores. As follows:
                                                    key: term
                                                    value: bm25 idf score
        """
        idf = {}
        for term in list_of_tokens:
            if term in self.index.df.keys():
                n_ti = self.index.df[term]
                idf[term] = math.log(1 + (self.N - n_ti + 0.5) / (n_ti + 0.5))
            else:
                pass
        return idf

    def search(self, queries, N=3):
        """
        This function calculate the bm25 score for given query and document.
        We need to check only documents which are 'candidates' for a given query.
        This function return a dictionary of scores as the following:
                                                                    key: query_id
                                                                    value: a ranked list of pairs (doc_id, score) in the length of N.

        Parameters:
        -----------
        query: list of token representing the query. For example: ['look', 'blue', 'sky']
        doc_id: integer, document id.

        Returns:
        -----------
        score: float, bm25 score.
        """
        for term in queries:
            if term in list(self.index.df.keys()):
                self.words.append(term)
                self.pls.append(self.index.read_posting_list_dict(term))
        self.idf = self.calc_idf(queries)
        scores_sorted = heapq.nlargest(N, self._score(queries, 0).items(), key=lambda x: x[1])
        return scores_sorted

        # raise NotImplementedError()

    def _score(self, query, doc_id):
        """
        This function calculate the bm25 score for given query and document.

        Parameters:
        -----------
        query: list of token representing the query. For example: ['look', 'blue', 'sky']
        doc_id: integer, document id.

        Returns:
        -----------
        score: float, bm25 score.
        """
        score = 0.0
        scores_dict = {}
        for term in query:
            if term in list(self.index.df.keys()):
                tf = self.index.read_posting_list_dict(term)
                for doc_id in tf.keys():
                    # mone = self.idf[term] * tf[doc_id] * (self.k1 + 1)
                    # mechane = tf[doc_id] + self.k1 * (1 - self.b + self.b * self.index.DL[doc_id] / self.AVGDL)
                    score = (self.idf[term] * tf[doc_id] * (self.k1 + 1)) / (tf[doc_id] + self.k1 * (1 - self.b + self.b * self.index.DL[doc_id] / self.AVGDL))
                    scores_dict[doc_id] = scores_dict.get(doc_id, 0) + score
        return scores_dict