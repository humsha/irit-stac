# Author: Eric Kow
# License: CeCILL-B (French BSD3-like)

"""
run an experiment
"""

from __future__ import print_function
from os import path as fp
from collections import namedtuple
import argparse
import json
import os
import sys

from attelo.args import\
    args_to_decoder, args_to_phrasebook
from attelo.decoding import DataAndModel
from attelo.io import\
    read_data, load_model
import attelo.cmd as att

from attelo.harness.config import CliArgs
from attelo.harness.report import CountIndex
from attelo.harness.util import\
    timestamp, call, force_symlink

from ..local import\
    EVALUATION_CORPORA, EVALUATIONS, ATTELO_CONFIG_FILE
from ..util import\
    exit_ungathered, latest_tmp, link_files

NAME = 'evaluate'
_DEBUG = 0

# pylint: disable=pointless-string-statement
LoopConfig = namedtuple("LoopConfig",
                        ["eval_dir",
                         "scratch_dir",
                         "fold_file",
                         "dataset"])
"that which is common to outerish loops"

DataConfig = namedtuple("DataConfig", "attach relate")
"data tables we have read"
# pylint: enable=pointless-string-statement

# ---------------------------------------------------------------------
# user feedback
# ---------------------------------------------------------------------


def _eval_banner(econf, lconf, fold):
    """
    Which combo of eval parameters are we running now?
    """
    rname = econf.learner.relate
    learner_str = econf.learner.attach + (":" + rname if rname else "")
    return "\n".join(["----------" * 3,
                      "fold %d [%s]" % (fold, lconf.dataset),
                      "learner(s): %s" % learner_str,
                      "decoder: %s" % econf.decoder.decoder,
                      "----------" * 3])


def _corpus_banner(lconf):
    "banner to announce the corpus"
    return "\n".join(["==========" * 7,
                      lconf.dataset,
                      "==========" * 7])


def _fold_banner(lconf, fold):
    "banner to announce the next fold"
    return "\n".join(["==========" * 6,
                      "fold %d [%s]" % (fold, lconf.dataset),
                      "==========" * 6])

# ---------------------------------------------------------------------
# attelo config
# ---------------------------------------------------------------------


# pylint: disable=too-many-instance-attributes, too-few-public-methods
class FakeEvalArgs(CliArgs):
    """
    Fake argparse object (to be subclassed)
    Things in common between attelo learn/decode
    """
    def __init__(self, lconf, econf, fold):
        self.lconf = lconf
        self.econf = econf
        self.fold = fold
        super(FakeEvalArgs, self).__init__()

    def parser(self):
        """
        The argparser that would be called on context manager
        entry
        """
        psr = argparse.ArgumentParser()
        att.enfold.config_argparser(psr)

    def argv(self):
        econf = self.econf
        lconf = self.lconf
        fold = self.fold

        model_file_a = _eval_model_path(lconf, econf, fold, "attach")
        model_file_r = _eval_model_path(lconf, econf, fold, "relate")

        argv = [_eval_csv_path(lconf, "edu-pairs"),
                _eval_csv_path(lconf, "relations"),
                "--config", ATTELO_CONFIG_FILE,
                "--fold", fold,
                "--fold-file", lconf.fold_file,
                "--attachment-model", model_file_a,
                "--relation-model", model_file_r]
        return argv

    # pylint: disable=no-member
    def __exit__(self, ctype, value, traceback):
        "Tidy up any open file handles, etc"
        self.fold_file.close()
        super(FakeEvalArgs, self).__exit__(ctype, value, traceback)
    # pylint: enable=no-member


class FakeEnfoldArgs(CliArgs):
    """
    Fake argparse object that would be generated by attelo enfold
    """
    def __init__(self, lconf):
        self.lconf = lconf
        super(FakeEnfoldArgs, self).__init__()

    def parser(self):
        psr = argparse.ArgumentParser()
        att.enfold.config_argparser(psr)
        return psr

    def argv(self):
        """
        Command line arguments that would correspond to this
        configuration
        :rtype: `[String]`
        """
        lconf = self.lconf
        args = [_eval_csv_path(lconf, "edu-pairs"),
                "--config", ATTELO_CONFIG_FILE,
                "--output", lconf.fold_file]
        return args

    # pylint: disable=no-member
    def __exit__(self, ctype, value, traceback):
        "Tidy up any open file handles, etc"
        self.output.close()
        super(FakeEnfoldArgs, self).__exit__(ctype, value, traceback)
    # pylint: enable=no-member


class FakeLearnArgs(FakeEvalArgs):
    """
    Fake argparse object that would be generated by attelo learn.
    """
    def __init__(self, lconf, econf, fold):
        super(FakeLearnArgs, self).__init__(lconf, econf, fold)

    def parser(self):
        psr = argparse.ArgumentParser()
        att.learn.config_argparser(psr)
        return psr

    def argv(self):
        econf = self.econf
        args = super(FakeLearnArgs, self).argv()
        args.extend(["--learner", econf.learner.attach])
        if econf.learner.relate is not None:
            args.extend(["--relation-learner", econf.learner.relate])
        if econf.decoder is not None:
            args.extend(["--decoder", econf.decoder.decoder])
        return args


class FakeDecodeArgs(FakeEvalArgs):
    """
    Fake argparse object that would be generated by attelo decode
    """
    def __init__(self, lconf, econf, fold):
        super(FakeDecodeArgs, self).__init__(lconf, econf, fold)

    def parser(self):
        psr = argparse.ArgumentParser()
        att.decode.config_argparser(psr)
        return psr

    def argv(self):
        lconf = self.lconf
        econf = self.econf
        fold = self.fold
        args = super(FakeDecodeArgs, self).argv()
        args.extend(["--decoder", econf.decoder.decoder,
                     "--scores", _counts_file_path(lconf, econf, fold),
                     "--output", _decode_output_path(lconf, econf, fold)])
        return args

    # pylint: disable=no-member
    def __exit__(self, ctype, value, traceback):
        "Tidy up any open file handles, etc"
        self.scores.close()
    # pylint: enable=no-member
# pylint: enable=too-many-instance-attributes, too-few-public-methods


# ---------------------------------------------------------------------
# evaluation
# ---------------------------------------------------------------------

def _eval_csv_path(lconf, ext):
    """
    Path to data file in the evaluation dir
    """
    return os.path.join(lconf.eval_dir,
                        "%s.%s.csv" % (lconf.dataset, ext))


def _fold_dir_path(lconf, fold):
    "Scratch directory for working within a given fold"
    return os.path.join(lconf.scratch_dir, "fold-%d" % fold)


def _eval_model_path(lconf, econf, fold, mtype):
    "Model for a given loop/eval config and fold"
    lname = econf.learner.name
    fold_dir = _fold_dir_path(lconf, fold)
    return os.path.join(fold_dir,
                        "%s.%s.%s.model" % (lconf.dataset, lname, mtype))


def _counts_file_path(lconf, econf, fold):
    "Scores collected for a given loop and eval configuration"
    fold_dir = _fold_dir_path(lconf, fold)
    return os.path.join(fold_dir,
                        ".".join(["counts", econf.name, "csv"]))


def _decode_output_path(lconf, econf, fold):
    "Model for a given loop/eval config and fold"
    fold_dir = _fold_dir_path(lconf, fold)
    return os.path.join(fold_dir,
                        ".".join(["output", econf.name]))


def _index_file_path(parent_dir, lconf):
    """
    Create a blank count index file in the given directory,
    see `CountIndex` for how this is to be used
    """
    return os.path.join(parent_dir,
                        "count-index-%s.csv" % lconf.dataset)


def _score_file_path_prefix(parent_dir, lconf):
    """
    Path to a score file given a parent dir.
    You'll need to tack an extension onto this
    """
    return fp.join(parent_dir, "scores-%s" % lconf.dataset)


def _maybe_learn(lconf, dconf, econf, fold):
    """
    Run the learner unless the model files already exist
    """
    fold_dir = _fold_dir_path(lconf, fold)
    if not os.path.exists(fold_dir):
        os.makedirs(fold_dir)

    with FakeLearnArgs(lconf, econf, fold) as args:
        phrasebook = args_to_phrasebook(args)
        fold_attach, fold_relate =\
            att.learn.select_fold(dconf.attach,
                                  dconf.relate,
                                  args,
                                  phrasebook)

        if fp.exists(args.attachment_model) and\
           fp.exists(args.relation_model):
            print("reusing %s model (already built)" % econf.learner.name,
                  file=sys.stderr)
            return

        att.learn.main_for_harness(args, fold_attach, fold_relate)


def _decode(lconf, dconf, econf, fold):
    """
    Run the decoder for this given fold
    """
    if fp.exists(_counts_file_path(lconf, econf, fold)):
        print("skipping %s/%s (already done)" % (econf.learner.name,
                                                 econf.decoder.name),
              file=sys.stderr)
        return

    fold_dir = _fold_dir_path(lconf, fold)
    if not os.path.exists(fold_dir):
        os.makedirs(fold_dir)
    with FakeDecodeArgs(lconf, econf, fold) as args:
        phrasebook = args_to_phrasebook(args)
        decoder = args_to_decoder(args)

        fold_attach, fold_relate =\
            att.decode.select_fold(dconf.attach, dconf.relate,
                                   args, phrasebook)
        attach = DataAndModel(fold_attach,
                              load_model(args.attachment_model))
        relate = DataAndModel(fold_relate,
                              load_model(args.relation_model))

        att.decode.main_for_harness(args, phrasebook, decoder,
                                    attach, relate)


def _generate_fold_file(lconf, dconf):
    """
    Generate the folds file
    """
    with FakeEnfoldArgs(lconf) as args:
        att.enfold.main_for_harness(args, dconf.attach)


def _mk_report(parent_dir, lconf, idx_file):
    "Generate reports for scores"
    score_prefix = _score_file_path_prefix(parent_dir, lconf)
    json_file = score_prefix + ".json"
    pretty_file = score_prefix + ".txt"

    with open(pretty_file, "w") as pretty_stream:
        call(["attelo", "report",
              idx_file,
              "--json", json_file],
             stdout=pretty_stream)

    print("Scores summarised in %s" % pretty_file,
          file=sys.stderr)


def _do_tuple(lconf, dconf, econf, fold):
    """
    Run a single combination of parameters (innermost block)
    Return a counts index entry
    """
    cfile = _counts_file_path(lconf, econf, fold)
    _maybe_learn(lconf, dconf, econf, fold)
    _decode(lconf, dconf, econf, fold)
    return {"config": econf.name,
            "fold": fold,
            "counts_file": cfile}


def _do_fold(lconf, dconf, fold, idx):
    """
    Run all learner/decoder combos within this fold
    """
    fold_dir = _fold_dir_path(lconf, fold)
    score_prefix = _score_file_path_prefix(fold_dir, lconf)
    if fp.exists(score_prefix + ".txt"):
        print("Skipping fold %d (already run)" % fold,
              file=sys.stderr)
        return

    print(_fold_banner(lconf, fold), file=sys.stderr)
    if not os.path.exists(fold_dir):
        os.makedirs(fold_dir)
    fold_idx_file = _index_file_path(fold_dir, lconf)
    with CountIndex(fold_idx_file) as fold_idx:
        for econf in EVALUATIONS:
            print(_eval_banner(econf, lconf, fold), file=sys.stderr)
            idx_entry = _do_tuple(lconf, dconf, econf, fold)
            idx.writerow(idx_entry)
            fold_idx.writerow(idx_entry)
    fold_dir = _fold_dir_path(lconf, fold)
    _mk_report(fold_dir, lconf, fold_idx_file)


def _do_corpus(lconf):
    "Run evaluation on a corpus"
    print(_corpus_banner(lconf), file=sys.stderr)

    attach_file = _eval_csv_path(lconf, "edu-pairs")
    relate_file = _eval_csv_path(lconf, "relations")
    if not os.path.exists(attach_file):
        exit_ungathered()
    data_attach, data_relate =\
        read_data(attach_file, relate_file, verbose=True)
    dconf = DataConfig(attach=data_attach,
                       relate=data_relate)

    _generate_fold_file(lconf, dconf)

    with open(lconf.fold_file) as f_in:
        folds = frozenset(json.load(f_in).values())

    idx_file = _index_file_path(lconf.scratch_dir, lconf)
    with CountIndex(idx_file) as idx:
        for fold in folds:
            _do_fold(lconf, dconf, fold, idx)
    _mk_report(lconf.eval_dir, lconf, idx_file)

# ---------------------------------------------------------------------
# main
# ---------------------------------------------------------------------


def config_argparser(psr):
    """
    Subcommand flags.

    You should create and pass in the subparser to which the flags
    are to be added.
    """
    psr.set_defaults(func=main)
    psr.add_argument("--resume",
                     default=False, action="store_true",
                     help="resume previous interrupted evaluation")


def _create_eval_dirs(args, data_dir):
    """
    Return eval and scatch directory paths
    """

    eval_current = fp.join(data_dir, "eval-current")
    scratch_current = fp.join(data_dir, "scratch-current")

    if args.resume:
        if not fp.exists(eval_current) or not fp.exists(scratch_current):
            sys.exit("No currently running evaluation to resume!")
        else:
            return eval_current, scratch_current
    else:
        tstamp = "TEST" if _DEBUG else timestamp()
        eval_dir = fp.join(data_dir, "eval-" + tstamp)
        if not fp.exists(eval_dir):
            os.makedirs(eval_dir)
            link_files(data_dir, eval_dir)
            force_symlink(fp.basename(eval_dir), eval_current)
        elif not _DEBUG:
            sys.exit("Try again in literally one second")

        scratch_dir = fp.join(data_dir, "scratch-" + tstamp)
        if not fp.exists(scratch_dir):
            os.makedirs(scratch_dir)
            force_symlink(fp.basename(scratch_dir), scratch_current)

        return eval_dir, scratch_dir


def main(args):
    """
    Subcommand main.

    You shouldn't need to call this yourself if you're using
    `config_argparser`
    """
    data_dir = latest_tmp()
    if not os.path.exists(data_dir):
        exit_ungathered()
    eval_dir, scratch_dir = _create_eval_dirs(args, data_dir)

    with open(os.path.join(eval_dir, "versions-evaluate.txt"), "w") as stream:
        call(["pip", "freeze"], stdout=stream)

    for corpus in EVALUATION_CORPORA:
        dataset = os.path.basename(corpus)
        fold_file = os.path.join(eval_dir,
                                 "folds-%s.json" % dataset)
        lconf = LoopConfig(eval_dir=eval_dir,
                           scratch_dir=scratch_dir,
                           fold_file=fold_file,
                           dataset=dataset)
        _do_corpus(lconf)
