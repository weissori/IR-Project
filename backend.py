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
            sim = tuples.get(doc_id, 0)
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
    bin_anchor, _ = search_anchor(anchor_index, tokens)
    bin_title, _ = search_title(title_index, tokens)
    if len(tokens) <= 1:
        bodies, _ = search_body(body_index, tokens, 5)
        bin_body = binary_body(body_index, tokens)
        scores = []
        for doc_id in bin_body:
            anchor_score = (bin_anchor.get(doc_id, 0) + 1) ** 3
            title_score = (bin_title.get(doc_id, 0) + 1) ** 10
            scores.append([doc_id, bodies[doc_id] * page_rank.get(doc_id, 0) * page_views.get(doc_id,0) * anchor_score * title_score / 1000000])
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

# def train_ir(train_set):
#     train_tokens = {}
#     for query in train_set:
#         tokens = [token.group() for token in RE_WORD.finditer(query.lower())]
#         not_stopwords = ''
#         for token in tokens:
#             if token not in all_stopwords:
#                 not_stopwords += token
#         train_tokens[not_stopwords] = query
#     return train_tokens

def map40(query, docs):
    train_tokens = train_ir(train_set)
    correct = 0
    query_str = ''
    for token in query:
        if token in all_stopwords:
            continue
        query_str += token
    if train_tokens.get(query_str, 0) == 0:
        return train_tokens, query_str
    docs_list = train_set[train_tokens.get(query_str, 0)]
    for doc in docs[:40]:
        if doc in docs_list:
            correct += 1
    return correct/40, query_str, docs_list

train_set = {"best marvel movie": [57069491, 65967176, 42163310, 878659, 27306717, 60952488, 60744481, 66111204, 41974555, 1074657, 41677925, 61073786, 43603241, 37497391, 44240443, 56289553, 55935213, 17296107, 60616450, 60774345, 41974496, 46208997, 5676692, 10589717, 5027882, 36439749, 59892, 33700618, 66423851, 55511148, 61651800, 58481694, 60283633, 48530084, 612052, 60754840, 22144990, 12673434, 56848986, 29129051, 709344, 44254295, 56289672, 33463661, 11891556], "How do kids come to world?": [25490788, 6271835, 51046955, 83449, 46105, 56921904, 4827661, 5591344, 615418, 48490547, 36827305, 128987, 11436091, 15474, 11263877, 6236554, 30640885, 296627, 2535885, 1072968, 494299, 56480301, 1380383, 101942, 4332628, 14694092, 634139, 194687, 1151454, 35072597, 24470328, 42130800, 884998, 25084664, 79449, 43033258, 72214, 18863597, 73165, 1908019, 46504485, 29384326, 1357127, 387703, 19698110, 636806, 1833777, 239259, 44311171, 694630], "Information retrieval": [15271, 50716473, 19988623, 731640, 1185840, 442684, 24997830, 10179411, 39000674, 14473878, 33407925, 24963841, 509628, 261193, 18550455, 4694434, 11486091, 16635934, 296950, 38156944, 14109784, 20948989, 3781784, 5818361, 10328235, 14343887, 9511414, 743971, 10218640, 35804330, 7872152, 21106742, 36794719, 509624, 25130414, 25959000, 762092, 48317971, 25957127, 56598843], "LinkedIn": [970755, 50191962, 41726116, 3591502, 62976368, 36070366, 22291643, 31403505, 27769500, 57147095, 25311421, 53321154, 40413203, 63641225, 35549457], "How to make coffee?": [4604645, 273707, 300805, 604727, 19619306, 30860428, 26731675, 5212064, 667037, 6826364, 215424, 47660, 8728856, 63520964, 273700, 49099835, 63534797, 4506407, 31824340, 3785715, 5964683, 482824, 12343966, 28890200, 300784, 1646753, 408360, 1623162, 1566948, 68117784, 38579961, 8866584, 6887661, 5612891, 54459918, 2461806, 6332026, 3639440, 366244, 1301881, 5286885, 321546, 2898609, 838057, 2165666, 39228613], "Ritalin": [205878, 8802530, 13594085, 45690249, 10671710, 56961277, 22611786, 5721484, 6428730, 1790029, 649100, 2495940, 7432624, 5497377, 608718, 57068567, 23891416, 66391, 50762105, 1546447, 32325617, 6281833, 25164479, 2580091, 47956615, 964614, 57762, 7594242, 2424129, 4387617, 24754461, 1832706, 40542151, 52780757, 1598204, 463961, 1186041, 42815113], "How to make wine at home?": [373172, 32961, 485220, 36029170, 13824744, 21991369, 4378282, 8608425, 61014433, 22216378, 1455948, 8177057, 19561784, 1045027, 927688, 20810258, 1041458, 29324283, 223834, 1417287, 466664, 19600890, 1039412, 683094, 1807097, 928516, 753886, 22777652, 5222577, 14713963, 748887, 617040, 4554556, 20185928, 20790067, 146918, 31704630, 8778890, 904269, 14825456, 1046870, 32186253, 5222704, 143177, 10998, 890025, 24674258, 15468138, 14067073, 3031996], "Most expensive city in the world": [63946361, 3928523, 11947794, 1664254, 9299090, 2376810, 18402, 172538, 19058, 35368654, 32706, 49749249, 27862, 22309, 24724090, 522934, 22989, 645042, 220886, 33508970, 36511, 15218891, 10992, 27318, 94167, 390875, 7780, 20206, 19004, 17867, 12301026, 19261, 65708464, 31326350, 19189, 5299184, 14563484, 12521, 302201, 26976, 45470, 352844, 56114, 41940, 85232, 17306237], "India": [14533, 13890, 7564733, 20611562, 4208015, 14598, 5864614, 848489, 495343, 141896, 17774253, 3574003, 14745, 1472206, 3315459, 23397776, 678583, 1552939, 19189, 43281, 227809, 1996872, 26457880, 14580, 293133, 275047, 764545, 1683930, 553883, 2198463, 40010153, 803842, 226804, 42737, 208589, 407754, 44275267, 315776, 855820, 1544482, 602639, 1186115, 720662, 10710364, 47905, 295335, 231623, 1193781, 13652], "how to make money fast?": [17362858, 846772, 43250171, 8957449, 60739751, 17418777, 43030666, 1276547, 48732, 13681, 4416646, 7555986, 32595633, 1527716, 41637982, 400777, 35666788, 63121, 19390, 2763667, 12789839, 4090453, 23830729, 44379765, 63809606, 45332, 2913859, 407288, 208286, 34307401, 29681566, 65228, 28082913], "Netflix": [175537, 34075129, 56312051, 65741484, 50276542, 65595607, 65741473, 60156461, 66299065, 9399111, 65073808, 22726888, 42433292, 64522550, 65877791, 65539844, 47048067, 58411201, 32670973, 52086235, 49545674, 57041239, 61963380, 56312054, 50602056, 62387071, 34119966, 55762562, 57442012, 33757091, 57376607, 62220931, 61972257, 50137861], "Apple computer": [856, 19006979, 2593693, 1344, 4478297, 2275, 17997437, 2116, 2117, 15295713, 21347643, 73262, 50865995, 2786155, 32327247, 25122, 548115, 758738, 248101, 5078775, 21694, 18640, 1159939, 400593, 2020710, 46728817, 17826747, 345354, 1492625, 418482, 233780, 5285468, 177113, 255275, 1575166, 1005263, 15183570, 24886, 27848, 254496, 46668814, 77118], "The Simpsons": [29838, 9306179, 22423628, 74813, 1424178, 1466966, 1625137, 60534017, 140332, 49387265, 292279, 4939306, 4939519, 64072, 64276, 4939369, 144500, 5451605, 40008080, 10765975, 4939277, 20942925, 4776530, 4939334, 64300, 188572, 4939408, 11028525, 64277, 4939240, 88235, 1545561, 2342096, 12517846, 46626411, 3038969, 462389, 14040227, 2741433], "World cup": [11370, 17742072, 8821389, 33727, 13327177, 4723188, 29868391, 59707, 16383, 25406, 7239, 60986, 59729, 183628, 2996777, 1864131, 3482503, 36581929, 8258172, 1248592, 45271353, 39812824, 656933, 16966712, 39367087, 1347093, 12312312, 4743361, 1618638, 951198, 27226732, 168079, 61629, 43255516, 27807301, 57240806, 32516422, 57918706, 1806428, 26814387, 57918704, 19537336, 41722878, 11049], "How to lose weight?": [400199, 8460, 26639763, 8581665, 28541957, 727293, 1017976, 11665493, 33825347, 84252, 1148926, 27148738, 2883760, 56885915, 65004286, 3549164, 6319249, 30687447, 410007, 2029766, 56435, 4748844, 18168862, 31429041, 9972157, 40925771, 32051848, 35281209, 11884255, 1149933, 44442017, 17659030, 277790, 67730903, 23609959, 54712, 12523816, 1256165, 49492407, 28396636, 45280337, 791546, 61480251, 27300359], "Java": [15881, 69336, 4718446, 7771171, 16389, 13593, 42871, 42870, 230828, 24920873, 5516020, 1131136, 15628, 127604, 38321273, 269441, 731735, 9845, 1414212, 7955681, 30120784, 663788, 5457138, 611589, 53078721, 453584, 320443, 1173053, 3901428, 4093054, 135063, 5863400, 26257672, 42869, 1179384, 16529, 49003520, 4294832, 17521476, 1326984, 43826, 651278, 11125049, 1107856, 417018, 177789], "Air Jordan": [1394509, 58209447, 3647739, 4253801, 20455, 67838974, 13365219, 18998781, 60601430, 2310146, 265033, 2920109, 6722408, 3097723, 14665244, 3890370, 62741501, 1371219, 32963694, 9998569, 33649690, 13961748, 1513732, 105344, 3939524, 13618859, 15416945, 33237492, 45404721], "how to deal with depression?": [19064282, 8389, 4041101, 20448627, 840273, 3440273, 16407460, 25258288, 1295947, 30846934, 22481627, 20529621, 18550003, 60611538, 66811, 42730418, 2721889, 13190302, 2367697, 18176448, 2353519, 16360289, 717119, 14325087, 175357, 21211994, 39218436, 33310173, 60457349, 1500618, 19477293, 2685269, 52316, 57688, 1879108, 4531, 43600438, 5144613, 34753948, 63292683, 43875835, 63499429, 234796, 49233423, 255475, 2891701, 3762294, 47677054, 13877205], "How do you make gold": [12240, 1230653, 20063724, 1291393, 25918508, 56226, 1686492, 402244, 2015573, 1386629, 63280480, 12095348, 3519942, 390698, 39740796, 1356272, 1385632, 2732267, 251087, 886856, 2927992, 39639653, 6890967, 180211, 15457257, 37412, 6109962, 45756, 7133952, 6996576, 23290471, 10865561, 1581831, 1020809, 19074264, 10847863, 62929, 3706246, 39320626, 67110306, 15739, 44712684, 23324, 27119, 6742209, 2526649, 13118408, 4047274, 34079193], "Marijuana": [1481886, 38310, 19920359, 168915, 175440, 20481920, 168917, 20566488, 14942276, 4512923, 145891, 31188467, 60920, 25905247, 49719430, 150113, 53428626, 5084026, 22707918, 53786507, 37646421, 19760623, 48640150, 20866399, 19357, 47642059, 27202445, 52356241, 59760961, 52183794, 52209645, 28985374, 49143075, 52211723, 53836251, 11164587, 52183921, 68188835, 52356136, 52228042, 52386414, 53871120, 49378648, 3045683, 47239576, 52356029, 24473585], "How to make hummus": [75065, 13607, 57146, 2243880, 48876576, 289691, 24230253, 47863605, 20657443, 22736969, 49643204, 3508935, 7489122, 3548013, 164311, 2578570, 1039663, 62166289, 3841447, 4925720, 56494240, 11287682, 453166, 8559295, 5033181, 682549, 11447140, 47863662, 1626287, 5334377, 37534432, 3099917, 2322115, 82789, 9513043, 607255, 317450, 14320, 7329519, 42006157, 13913, 31497735, 8564070, 3260137], "Winter": [34061, 8521120, 962053, 30276826, 20925895, 28483, 38950, 244878, 34069, 65602238, 33924, 33672235, 3548574, 109566, 66751284, 19431459, 211869, 3227879, 43343961, 1632099, 961505, 1221158, 1298502, 1088531, 200373, 22933429, 36480174, 18670284, 6170150, 58564, 3292487, 17349106, 1971153, 260683, 33634815, 16615604, 8778803, 65601132, 109565, 3060382, 1843684, 3719969, 1817908, 4886790, 19938267, 1799816, 9637495], "Rick and Morty": [41185040, 41283158, 65819511, 43794572, 57390230, 47762921, 49029294, 43794574, 67520032, 67830379, 51759111, 54046846, 55708102, 61805032, 41699729, 55339286, 49260717, 68010196, 51082764, 26091326, 49128142, 54802759, 55339299, 55339303, 52261594, 57314882, 63656365, 64413225, 49134382], "Natural Language processing": [21652, 67147, 98778, 40573, 37764426, 18784729, 1661566, 301999, 18863997, 64695824, 27837170, 43561218, 43771647, 61603971, 57932194, 62026514, 5561, 6650456, 21173, 32707853, 360030, 53358397, 32472154, 27857167, 563439, 20892159, 1732213, 1164, 56142183, 11147298, 4561188, 252008, 42799166, 10235, 1936537, 35715808, 14003441, 2891758, 36323189, 60360004], "World Cup 2022": [17742072, 11370, 29868391, 57240806, 57918704, 27226732, 57918701, 64112605, 57918697, 67608822, 51765484, 66040080, 57918706, 61872359, 8258172, 57918711, 3482503, 57918689, 11049, 59613812, 1248592, 62528055, 45271353, 59863995, 3556431, 65955719, 10822574], "Dolly the sheep": [9146, 1857574, 12054042, 42555506, 16285933, 52793670, 1631732, 9649607, 2082914, 17842616, 2828101, 1962277, 8716, 63031051, 1751707, 6910, 1140293, 14094, 168927, 2372209, 45485344, 39379960, 56398129, 1632972, 1321047, 6832430, 1567101, 383180, 192685, 53431353, 38889846, 1258132, 915258, 14020881, 48188481, 9556567, 1731036, 932553, 8394105, 18590036, 7932132], "Ciggarets": [38327, 73298, 35077599, 4870997, 30942, 50164035, 8655214, 11996885, 6003061, 312963, 17596651, 404590, 3015678, 20587357, 42877829], "What is the best place to live in?": [1664254, 48461477, 60333700, 33018516, 851512, 1649321, 22916979, 32028, 52749663, 31885991, 36040841, 33569489, 42881894, 1655287, 41940, 3367760, 5713554, 18110, 124779, 125558, 14649921, 66351400, 32950054, 260376, 126805, 32706, 45222463, 23189729, 3535679, 5407, 1978628, 13774, 18112665, 55166, 1387207, 139176, 56114, 5201333, 33323927, 93961, 214452, 2973070, 19394651, 37325161, 19159283, 309890, 1998], "Elon musk": [909036, 65175052, 65212863, 5533631, 832774, 47190535, 53215263, 36971117, 53615490, 4335905, 66405413, 9988187, 45111627, 51237650, 52247588, 48778030, 2614738, 41360413, 39636436, 803102, 31406060, 195809, 55382641], "How do you breed flowers?": [30876044, 16128216, 31552410, 200646, 41244, 407234, 6614349, 1183979, 233609, 13799261, 4576465, 63539530, 971961, 42680256, 33336442, 33131935, 55819873, 893280, 18967, 4226137, 68213121, 1028614, 63484108, 1104639, 39683, 63180590, 1390689, 73421, 26537, 167906, 3288269, 277231, 5902061, 57141131, 49883395, 19049100, 1071613, 18691124, 630109, 1392524, 76143, 430347, 66556, 35646178, 224785, 57374888, 267657, 57622]}
# print(train_ir(train_set))
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