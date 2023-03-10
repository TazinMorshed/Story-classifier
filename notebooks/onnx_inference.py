# -*- coding: utf-8 -*-
"""onnx_inference.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/1vxXc7NH90Kmt4VcJO4JylqnHPxZX17wh
"""

# Commented out IPython magic to ensure Python compatibility.
# %reload_ext autoreload
# %autoreload 2
# %matplotlib inline

! pip install -q transformers[sentencepiece] fastai ohmeow-blurr nbdev

! pip install -q onnxruntime onnx==1.10.0 onnxruntime-gpu onnxruntime_tools

import torch
from transformers import AutoModelForSequenceClassification, AutoConfig
from fastai.text.all import *
from blurr.text.data.all import *
from blurr.text.modeling.all import *

from tqdm.notebook import tqdm
import numpy as np

import json
  

f = open('/content/drive/MyDrive/Imdb_data/imdb.json')
data = json.load(f)

print(data)

import pandas as pd
df = pd.DataFrame(data=data, columns=data[0].keys())
df.tail(2)

df = df.dropna().reset_index(drop=True)
df.shape

genres_list = df.genres.to_list()
genre_count = {}
for genres in genres_list:
  genre_list = eval(str(genres))
  for genre in genre_list:
    if genre in genre_count.keys():
      genre_count[genre] += 1
    else:
      genre_count[genre] = 1
print(f"Number of Genres: {len(genre_count)}")
print(genre_count)

threshold = int(len(df) * 0.01)
rare_genres = [key for key, value in genre_count.items() if value < threshold]
len(rare_genres)

genres_list = df.genres.to_list()
revised_genre_list = []
indices_to_drop = []

for idx, genres in enumerate(genres_list):
  genre_list = eval(str(genres))
  revised_genres = []

  for genre in genre_list:
    if genre not in rare_genres:
      revised_genres.append(genre)

  if len(revised_genres) == 0:
    indices_to_drop.append(idx)
  else:
    revised_genre_list.append(revised_genres)

df = df.drop(indices_to_drop).reset_index(drop=True)
df['revised_genres'] = revised_genre_list
df.shape

revised_genres_list = df.revised_genres.to_list()
revised_genre_count = {}
for genres in revised_genres_list:
  genre_list = genres
  for genre in genre_list:
    if genre in revised_genre_count.keys():
      revised_genre_count[genre] += 1
    else:
      revised_genre_count[genre] = 1
print(f"Number of Genres: {len(revised_genre_count)}")

encode_genre_types = { key: idx for idx, (key, value) in enumerate(revised_genre_count.items())}
with open("/content/drive/MyDrive/imdb_onnx/genre_types_encoded.json", "w") as fp:
  json.dump(encode_genre_types, fp)

# We need this because for multilabel classification all genres have possibility to be present in the predictions
categorical_genre_list = []
revised_genres_list = df.revised_genres.to_list()

for revised_genres in revised_genres_list:
  categorical_list = [0] * len(encode_genre_types)
  for genre in revised_genres:
    genre_type_index = encode_genre_types[genre] 
    categorical_list[genre_type_index] = 1
  categorical_genre_list.append(categorical_list)

df['genre_cat_list'] = categorical_genre_list
df.shape

labels = list(encode_genre_types.keys())
len(labels), labels[:5]

"""# Data Split"""

splitter = RandomSplitter(valid_pct=0.1, seed=42)
train_ids, valid_ids = splitter(df)
len(train_ids), len(valid_ids)

valid_df = df.loc[valid_ids]
valid_df.head(2)

model_path = "/content/drive/MyDrive/imdb_everything/models/imdb-classifier-stage-1.pkl"
learner_inf = load_learner(model_path)

learner_inf.blurr_predict("Dragons and Monsters")

learner_inf.blurr_predict("Dragons and Monsters")[0]['labels']

"""# Evaluation"""

from sklearn import metrics

def metric_measures(test_df, preds):

  targets = [np.asarray(target) for target in test_df['genre_cat_list'].to_list()]
  outputs = [np.asarray(pred) for pred in preds]


  accuracy = metrics.accuracy_score(targets, outputs)
  f1_score_micro = metrics.f1_score(targets, outputs, average='micro')
  f1_score_macro = metrics.f1_score(targets, outputs, average='macro')

  print(f"F1 Score (Micro) = {f1_score_micro}")
  print(f"F1 Score (Macro) = {f1_score_macro}")

  return

preds = []
for idx, row in tqdm(valid_df.iterrows(), total=len(valid_df)):
  desc = row['description']
  labels = learner_inf.blurr_predict(desc)[0]['labels']
  pred_genres = [0] * len(encode_genre_types)
  for label in labels:
    pred_genres[encode_genre_types[label]] = 1
  preds.append(pred_genres)

preds[0][:20]

metric_measures(valid_df, preds)

"""# Convert to ONNX"""

model_path = "/content/drive/MyDrive/imdb_everything/models/imdb-classifier-stage-1.pkl"
learner_inf = load_learner(model_path)

learner_inf.model.hf_model

classifier = learner_inf.model.hf_model.eval()

torch.onnx.export(
    classifier, 
    torch.LongTensor([[0] * 512]),
    '/content/drive/MyDrive/imdb_onnx/models/imdb-classifier.onnx',
    input_names=['input_ids'],
    output_names=['output'],
    opset_version=13,
    dynamic_axes={
        'input_ids': {0: 'batch_size', 1: 'sequence_len'},
        'output': {0: 'batch_size'}
    }
)

from onnxruntime.quantization import quantize_dynamic, QuantType

onnx_model_path = '/content/drive/MyDrive/imdb_onnx/models/imdb-classifier.onnx'
quantized_onnx_model_path = '/content/drive/MyDrive/imdb_onnx/models/imdb-classifier-quantized.onnx'

quantize_dynamic(
    onnx_model_path,
    quantized_onnx_model_path,
    weight_type=QuantType.QUInt8,
)

"""# ONNX inference

## Normal ONNX
"""

import onnxruntime as rt
from transformers import AutoTokenizer
import torch

tokenizer = AutoTokenizer.from_pretrained("distilroberta-base")

class_labels = list(encode_genre_types.keys())

inf_session = rt.InferenceSession('/content/drive/MyDrive/imdb_onnx/models/imdb-classifier.onnx')
input_name = inf_session.get_inputs()[0].name
output_name = inf_session.get_outputs()[0].name

preds = []
for idx, row in tqdm(valid_df.iterrows(), total=valid_df.shape[0]):
  desc = row['description']
  input_ids = tokenizer(desc)['input_ids'][:512]

  probs = inf_session.run([output_name], {input_name: [input_ids]})[0]
  probs = torch.FloatTensor(probs)

  masks = torch.sigmoid(probs) >= 0.5
  labels = [class_labels[idx] for idx, mask in enumerate(masks[0]) if mask]

  pred_genres = [0] * len(encode_genre_types)
  for label in labels:
    pred_genres[encode_genre_types[label]] = 1
  preds.append(pred_genres)

metric_measures(valid_df, preds)

"""# Quantized ONNX"""

import onnxruntime as rt
from transformers import AutoTokenizer
import torch

tokenizer = AutoTokenizer.from_pretrained("distilroberta-base")


with open("/content/drive/MyDrive/imdb_onnx/genre_types_encoded.json", "r") as fp:
  encode_genre_types = json.load(fp)

class_labels = list(encode_genre_types.keys())

inf_session = rt.InferenceSession('/content/drive/MyDrive/imdb_onnx/models/imdb-classifier-quantized.onnx')
input_name = inf_session.get_inputs()[0].name
output_name = inf_session.get_outputs()[0].name

preds = []
for idx, row in tqdm(valid_df.iterrows(), total=valid_df.shape[0]):
  desc = row['description']
  input_ids = tokenizer(desc)['input_ids'][:512]

  probs = inf_session.run([output_name], {input_name: [input_ids]})[0]
  probs = torch.FloatTensor(probs)

  masks = torch.sigmoid(probs) >= 0.5
  labels = [class_labels[idx] for idx, mask in enumerate(masks[0]) if mask]

  pred_genres = [0] * len(encode_genre_types)
  for label in labels:
    pred_genres[encode_genre_types[label]] = 1
  preds.append(pred_genres)

metric_measures(valid_df, preds) #

