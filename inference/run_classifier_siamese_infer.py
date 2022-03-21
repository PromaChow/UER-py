"""
  This script provides an example to wrap UER-py for classification inference.
"""
import sys
import os
import torch
import argparse
import collections
import torch.nn as nn

uer_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(uer_dir)

from uer.utils.constants import *
from uer.utils import *
from uer.utils.config import load_hyperparam
from uer.utils.seed import set_seed
from uer.model_loader import load_model
from uer.opts import infer_opts, tokenizer_opts
from finetune.run_classifier_siamese import SiameseClassifier


def batch_loader(batch_size, src, seg):
    src_a, src_b = src
    seg_a, seg_b = seg
    instances_num = src_a.size()[0]
    for i in range(instances_num // batch_size):
        src_a_batch = src_a[i * batch_size : (i + 1) * batch_size, :]
        src_b_batch = src_b[i * batch_size : (i + 1) * batch_size, :]
        seg_a_batch = seg_a[i * batch_size : (i + 1) * batch_size, :]
        seg_b_batch = seg_b[i * batch_size : (i + 1) * batch_size, :]
        yield (src_a_batch, src_b_batch), (seg_a_batch, seg_b_batch)
    if instances_num > instances_num // batch_size * batch_size:
        src_a_batch = src_a[instances_num // batch_size * batch_size :, :]
        src_b_batch = src_b[instances_num // batch_size * batch_size :, :]
        seg_a_batch = seg_a[instances_num // batch_size * batch_size :, :]
        seg_b_batch = seg_b[instances_num // batch_size * batch_size :, :]
        yield (src_a_batch, src_b_batch), (seg_a_batch, seg_b_batch)


def read_dataset(args, path):
    dataset, columns = [], {}
    with open(path, mode="r", encoding="utf-8") as f:
        for line_id, line in enumerate(f):
            if line_id == 0:
                line = line.rstrip("\r\n").split("\t")
                for i, column_name in enumerate(line):
                    columns[column_name] = i
                continue
            line = line.rstrip("\r\n").split("\t")
            text_a, text_b = line[columns["text_a"]], line[columns["text_b"]]
            src_a = args.tokenizer.convert_tokens_to_ids([CLS_TOKEN] + args.tokenizer.tokenize(text_a) + [SEP_TOKEN])
            src_b = args.tokenizer.convert_tokens_to_ids([CLS_TOKEN] + args.tokenizer.tokenize(text_b) + [SEP_TOKEN])
            seg_a = [1] * len(src_a)
            seg_b = [1] * len(src_b)
            PAD_ID = args.tokenizer.convert_tokens_to_ids([PAD_TOKEN])[0]

            if len(src_a) >= args.seq_length:
                src_a = src_a[:args.seq_length]
                seg_a = seg_a[:args.seq_length]
            while len(src_a) < args.seq_length:
                src_a.append(PAD_ID)
                seg_a.append(0)

            if len(src_b) >= args.seq_length:
                src_b = src_b[:args.seq_length]
                seg_b = seg_b[:args.seq_length]
            while len(src_b) < args.seq_length:
                src_b.append(PAD_ID)
                seg_b.append(0)

            dataset.append(((src_a, src_b), (seg_a, seg_b)))

    return dataset


def main():
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    infer_opts(parser)

    parser.add_argument("--labels_num", type=int, required=True,
                        help="Number of prediction labels.")
    tokenizer_opts(parser)

    parser.add_argument("--output_logits", action="store_true", help="Write logits to output file.")
    parser.add_argument("--output_prob", action="store_true", help="Write probabilities to output file.")

    args = parser.parse_args()

    # Load the hyperparameters from the config file.
    args = load_hyperparam(args)

    # Build tokenizer.
    args.tokenizer = str2tokenizer[args.tokenizer](args)

    # Build classification model and load parameters.
    args.soft_targets, args.soft_alpha = False, False
    model = SiameseClassifier(args)
    model = load_model(model, args.load_model_path)

    # For simplicity, we use DataParallel wrapper to use multiple GPUs.
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = model.to(device)
    if torch.cuda.device_count() > 1:
        print("{} GPUs are available. Let's use them.".format(torch.cuda.device_count()))
        model = torch.nn.DataParallel(model)

    dataset = read_dataset(args, args.test_path)

    src_a = torch.LongTensor([example[0][0] for example in dataset])
    src_b = torch.LongTensor([example[0][1] for example in dataset])
    seg_a = torch.LongTensor([example[1][0] for example in dataset])
    seg_b = torch.LongTensor([example[1][1] for example in dataset])

    batch_size = args.batch_size
    instances_num = src_a.size()[0]

    print("The number of prediction instances: ", instances_num)

    model.eval()

    with open(args.prediction_path, mode="w", encoding="utf-8") as f:
        f.write("label")
        if args.output_logits:
            f.write("\t" + "logits")
        if args.output_prob:
            f.write("\t" + "prob")
        f.write("\n")
        for i, (src_batch, seg_batch) in enumerate(batch_loader(batch_size, (src_a, src_b), (seg_a, seg_b))):

            src_a_batch, src_b_batch = src_batch
            seg_a_batch, seg_b_batch = seg_batch

            src_a_batch = src_a_batch.to(device)
            src_b_batch = src_b_batch.to(device)

            seg_a_batch = seg_a_batch.to(device)
            seg_b_batch = seg_b_batch.to(device)

            with torch.no_grad():
                _, logits = model((src_a_batch, src_b_batch), None, (seg_a_batch, seg_b_batch))

            pred = torch.argmax(logits, dim=1)
            pred = pred.cpu().numpy().tolist()
            prob = nn.Softmax(dim=1)(logits)
            logits = logits.cpu().numpy().tolist()
            prob = prob.cpu().numpy().tolist()

            for j in range(len(pred)):
                f.write(str(pred[j]))
                if args.output_logits:
                    f.write("\t" + " ".join([str(v) for v in logits[j]]))
                if args.output_prob:
                    f.write("\t" + " ".join([str(v) for v in prob[j]]))
                f.write("\n")


if __name__ == "__main__":
    main()
