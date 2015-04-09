'''
Paths to files used or generated by the test harness
'''

from os import path as fp

from attelo.util import (Team)


def attelo_doc_model_paths(lconf, rconf, fold):
    """
    Return attelo intra/intersentential model paths
    """
    return Team(attach=eval_model_path(lconf, rconf, fold, "attach"),
                relate=eval_model_path(lconf, rconf, fold, "relate"))


def attelo_sent_model_paths(lconf, rconf, fold):
    """
    Return attelo intra/intersentential model paths
    """
    return Team(attach=eval_model_path(lconf, rconf, fold, "sent-attach"),
                relate=eval_model_path(lconf, rconf, fold, "sent-relate"))


def eval_data_path(lconf, ext):
    """
    Path to data file in the evaluation dir
    """
    return fp.join(lconf.eval_dir,
                   "%s.%s" % (lconf.dataset, ext))


def features_path(lconf, stripped=False):
    """
    Path to the feature file in the evaluation dir
    """
    ext = 'relations.sparse'
    if stripped:
        ext += '.stripped'
    return eval_data_path(lconf, ext)


def vocab_path(lconf):
    """
    Path to the vocab file in the evaluation dir
    """
    return features_path(lconf) + '.vocab'


def edu_input_path(lconf):
    """
    Path to the feature file in the evaluation dir
    """
    return features_path(lconf) + '.edu_input'


def pairings_path(lconf):
    """
    Path to the pairings file in the evaluation dir
    """
    return features_path(lconf) + '.pairings'


def fold_dir_basename(fold):
    "Relative directory for working within a given fold"
    return "fold-%d" % fold


def fold_dir_path(lconf, fold):
    "Scratch directory for working within a given fold"
    return fp.join(lconf.scratch_dir,
                   fold_dir_basename(fold))


def combined_dir_path(lconf):
    "Scratch directory for working within the global config"
    return fp.join(lconf.scratch_dir, 'global')


def model_basename(lconf, rconf, mtype, ext):
    "Basic filename for a model"

    if 'dialogue-acts' in mtype:
        rsubconf = rconf
    elif 'attach' in mtype:
        rsubconf = rconf.attach
    else:
        rsubconf = rconf.relate or rconf.attach

    if rsubconf.payload == 'oracle':
        return 'oracle'
    else:
        template = '{dataset}.{learner}.{task}.{ext}'
        return template.format(dataset=lconf.dataset,
                               learner=rsubconf.key,
                               task=mtype,
                               ext=ext)


def eval_model_path(lconf, rconf, fold, mtype):
    "Model for a given loop/eval config and fold"
    if fold is None:
        parent_dir = combined_dir_path(lconf)
    else:
        parent_dir = fold_dir_path(lconf, fold)

    bname = model_basename(lconf, rconf, mtype, 'model')
    if bname == 'oracle':
        return bname
    else:
        return fp.join(parent_dir, bname)


def decode_output_basename(econf):
    "Model for a given loop/eval config and fold"
    return ".".join(["output", econf.key])


def decode_output_path(lconf, econf, fold):
    "Model for a given loop/eval config and fold"
    fold_dir = fold_dir_path(lconf, fold)
    return fp.join(fold_dir, decode_output_basename(econf))


def report_dir_basename(lconf):
    "Relative directory for a report directory"
    return "reports-%s" % lconf.dataset


def report_parent_dir_path(lconf, fold=None):
    "Directory that a report dir would be placed in"
    if fold is None:
        return lconf.scratch_dir
    else:
        return fold_dir_path(lconf, fold)


def report_dir_path(lconf, fold=None):
    """
    Path to a score file given a parent dir.
    You'll need to tack an extension onto this
    """
    return fp.join(report_parent_dir_path(lconf, fold),
                   report_dir_basename(lconf))


def model_info_path(lconf, rconf, fold=None, intra=False):
    """
    Path to the model output file
    """
    template = "discr-features{grain}.{learner}.txt"
    return fp.join(report_dir_path(lconf, fold),
                   template.format(grain='-sent' if intra else '',
                                   learner=rconf.key))
