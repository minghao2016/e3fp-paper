"""Filtering and file generation methods for cross-validation.

Author: Seth Axen
E-mail: seth.axen@gmail.com
"""
import os
import sys
import csv
import warnings
import itertools

import numpy as np
from scipy.sparse import issparse, lil_matrix, csr_matrix
# from sklearn import cross_validation as cv
from sklearn.metrics import auc as sk_auc
from sklearn.metrics import roc_curve, precision_recall_curve
try:
    from sklearn.exception import UndefinedMetricWarning
except ImportError:  # backwards compatibility with versions <0.17.2
    from sklearn.metrics.base import UndefinedMetricWarning
from seacore.util.library import SetValue
from ..pipeline import native_tuple_to_fprint
from ..sea_utils.util import molecules_to_lists_dicts, \
                             targets_to_dict, \
                             native_tuple_to_indices

csv.field_size_limit(sys.maxsize)

OUT_CSV_EXT_DEF = ".csv.gz"


def targets_to_array(targets, mol_list, dtype=np.int8, dense=False):
    if isinstance(targets, dict):
        targets_dict = targets
    else:
        targets_dict = targets_to_dict(targets)
    target_list = sorted(targets_dict.keys())
    target_num, mol_num = len(target_list), len(mol_list)

    mol_inds = {x: i for i, x in enumerate(mol_list)}
    target_mol_array = lil_matrix((target_num, mol_num), dtype=dtype)
    for i, target_key in enumerate(target_list):
        inds = [mol_inds[mol_name] for mol_name
                in targets_dict[target_key].cids]
        target_mol_array[i, inds] = True

    return target_mol_array.tocsr(), target_list


def molecules_to_array(molecules, mol_list, dtype=np.int8, dense=False):
    """Convert molecules to array or sparse matrix.

    Parameters
    ----------
    molecules : dict or string
        Molecules file or mol_list_dict.
    mol_list : list
        List of molecules, used to determine order of array.
    dtype : type, optional
        Numpy data type.
    dense : bool, optional
        Return dense array.

    Returns
    -------
    fp_array : ndarray or csr_matrix
        Row-based sparse matrix or ndarray containing fingerprints.
    mol_indices : dict
        Map from index in `mol_list` to list of row indices of fingerprints.
    """
    if isinstance(molecules, dict):
        mol_list_dict = molecules
    else:
        _, mol_list_dict, _ = molecules_to_lists_dicts(molecules)

    assert(set(mol_list_dict.keys()) == set(mol_list))

    bit_num = native_tuple_to_fprint(
        next(mol_list_dict.itervalues())[0]).bits
    mol_indices_dict = {}
    all_row_inds = []
    all_col_inds = []
    max_ind = 0
    for k, mol_name in enumerate(mol_list):
        native_tuples = mol_list_dict[mol_name]
        fp_num = len(native_tuples)
        row_inds = range(max_ind, max_ind + fp_num)
        mol_indices_dict[k] = row_inds
        max_ind += fp_num
        row_inds, col_inds = zip(
            *[(i, j) for i, n in itertools.izip(row_inds, native_tuples)
              for j in native_tuple_to_indices(n)])
        all_row_inds.extend(row_inds)
        all_col_inds.extend(col_inds)
    del mol_list_dict

    if dense:
        all_fps = np.zeros((max_ind, bit_num), dtype=dtype)
        all_fps[(all_row_inds, all_col_inds)] = True
    else:
        all_fps = csr_matrix(
            ([True] * len(all_row_inds), (all_row_inds, all_col_inds)),
            shape=(max_ind, bit_num), dtype=dtype)
    del all_row_inds, all_col_inds

    return all_fps, mol_indices_dict


def train_test_dicts_from_mask(mol_list_dict, mol_list, target_dict,
                               target_list, train_test_mask):
    test_inds = set(np.where(np.any(train_test_mask == 1, axis=0))[0])
    train_inds = set(np.where(np.any(train_test_mask == -1, axis=0))[0])
    test_mol_list_dict = {x: mol_list_dict[x] for i, x in enumerate(mol_list)
                          if i in test_inds}
    train_mol_list_dict = {x: mol_list_dict[x] for i, x in enumerate(mol_list)
                           if i in train_inds}

    test_target_dict = {}
    train_target_dict = {}
    for i, target_key in enumerate(target_list):
        test_mol_inds = np.where(train_test_mask[i, :] == 1)[0]
        test_mols = {mol_list[j] for j in test_mol_inds}
        sv = target_dict[target_key]
        pos_mols = set(sv.cids)
        test_target_dict[target_key] = SetValue(
            sv.name, sorted(pos_mols.intersection(test_mols)), sv.description)
        train_target_dict[target_key] = SetValue(
            sv.name, sorted(pos_mols.difference(test_mols)), sv.description)

    return (train_mol_list_dict, train_target_dict,
            test_mol_list_dict, test_target_dict)


def save_fprints_arr(fn, arr, mol_indices=None):
    """Save sparse array to file."""
    if not issparse(arr):
        arr = csr_matrix(arr)
    kwargs = {"dtype": [arr.dtype], "indices": arr.indices,
              "indptr": arr.indptr, "shape": arr.shape,
              "ndata": [len(arr.data)], "mol_indices": [mol_indices]}
    np.savez(fn, **kwargs)


def load_fprints_arr(fn, dense=False):
    """Load sparse array from file."""
    with np.load(fn) as f:
        dtype = f["dtype"][0]
        ndata = f["ndata"][0]
        indices = f["indices"]
        indptr = f["indptr"]
        shape = f["shape"]
        mol_indices = f["mol_indices"][0]
        data = np.ones(ndata, dtype=dtype)
        if dense:
            return (csr_matrix((data, indices, indptr),
                               shape=shape).toarray(), mol_indices)
        else:
            return (csr_matrix((data, indices, indptr), shape=shape),
                    mol_indices)


def filter_targets_by_molnum(targets_dict, n):
    """Return targets that have at least `n` binders."""
    return dict([(k, v) for k, v in targets_dict.iteritems()
                 if len(v.cids) >= n])


def merge_dicts(*ds):
    """Given dicts with (key, value), merge to dict with (key, [values])."""
    merged = {}
    for d in ds:
        for k, v in d.iteritems():
            merged.setdefault(k, []).append(v)
    return merged


def average_dict_values(*ds):
    """Given dicts with (key, value), return dict with (key, mean_value)."""
    merged = merge_dicts(*ds)
    return {k: np.mean(v) for k, v in merged.iteritems()}


def auc_dict_from_fp_tp_dict(d):
    """Calculate AUC from dict with tuple of FP rate and TP rate"""
    return {k: get_auc(v[0], v[1]) for k, v in d.iteritems()}


def logauc_dict_from_fp_tp_dict(d):
    """Calculate logAUC from dict with tuple of FP rate and TP rate"""
    return {k: get_logauc(v[0], v[1]) for k, v in d.iteritems()}


def get_delta_auc_dict(d1, d2):
    """Given 2 AUC dicts, subtract the AUC of the second from the first."""
    return {k: (v - d2[k]) for k, v in d1.iteritems() if k in d2}


def get_roc_prc_auc(true_false, metrics):
    """Calculate ROC and precision-recall curves (PRC) and their AUCs.

    Parameters
    ----------
    true_false : ndarray of int
        Array of 1s and 0s for what should be true hits and false hits,
        respectively.
    metrics : ndarray of double
        Array of metrics corresponding to positive hits.

    Returns
    -------
    roc : 3xN array of double
        The first two rows of the array, when plotted against each other, form
        an ROC curve, and the third is the thresholds corresponding to each
        point
    auroc : float
        Area under the ROC curve
    prc : tuple of 2xN array of double and 1-D array of double
        The first value in the tuple is the array with points on the PR curve.
        The second value are thresholds which correspond to some of these
        points (see scikit-learn documentation).
    auroc : float
        Area under the precision-recall curve
    """
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        try:
            fpr, tpr, roc_thresh = roc_curve(true_false, metrics,
                                             drop_intermediate=True)
        except UndefinedMetricWarning as e:
            raise
        auroc = get_auc(fpr, tpr)

        warnings.simplefilter("error")
        try:
            precision, recall, prc_thresh = precision_recall_curve(
                true_false, metrics)
        except RuntimeWarning as e:
            raise
        auprc = get_auc(recall, precision)

    return ((fpr, tpr, roc_thresh), auroc,
            (recall, precision, prc_thresh), auprc)


def get_auc(fp, tp, adjusted=False):
    """Calculate AUC from the FP and TP arrays of an ROC curve."""
    auc_val = sk_auc(fp, tp)
    if adjusted:
        auc_val -= 0.5
    return auc_val


def get_logroc(fp, tp, min_fp=0.001):
    lam_index = np.searchsorted(fp, min_fp)
    y = np.asarray(tp[lam_index:], dtype=np.double)
    x = np.asarray(fp[lam_index:], dtype=np.double)
    if (lam_index != 0):
        y = np.insert(y, 0, tp[lam_index - 1])
        x = np.insert(x, 0, min_fp)

    return np.log10(x), y


def get_logauc(fp, tp, min_fp=0.001, adjusted=False):
    """Calculate logAUC, the AUC of the semilog ROC curve.

    `logAUC_lambda` is defined as the AUC of the ROC curve where the x-axis
    is in log space. In effect, this zooms the ROC curve onto the earlier
    portion of the curve where various classifiers will usually be
    differentiated. The adjusted logAUC is the logAUC minus the logAUC of
    a random classifier, resulting in positive values for better-than-random
    and negative otherwise.

    Reference:
        - Mysinger et al. J. Chem. Inf. Model. 2010, 50, 1561-1573.
    """
    lam_index = np.searchsorted(fp, min_fp)
    y = np.asarray(tp[lam_index:], dtype=np.double)
    x = np.asarray(fp[lam_index:], dtype=np.double)
    if (lam_index != 0):
        y = np.insert(y, 0, tp[lam_index - 1])
        x = np.insert(x, 0, min_fp)

    dy = (y[1:] - y[:-1])
    with np.errstate(divide='ignore'):
        intercept = y[1:] - x[1:] * (dy / (x[1:] - x[:-1]))
        intercept[np.isinf(intercept)] = 0.
    norm = np.log10(1. / float(min_fp))
    areas = ((dy / np.log(10.)) + intercept * np.log10(x[1:] / x[:-1])) / norm
    logauc = np.sum(areas)
    if adjusted:
        logauc -= 0.144620062  # random curve logAUC
    return logauc


def get_youden(fp, tp):
    """Get Youden's index (height of ROC above random) for each point."""
    return tp - fp


def get_youden_index(fp, tp, return_coordinate=False):
    """Calculate Youden's J statistic from ROC curve.

    Youden's J statistic is defined as the maximum high of an ROC curve above
    the diagonal. Symbolically,
        J = max{TPR(FPR) - FPR}
    """
    youden = get_youden(fp, tp)
    index = np.argmax(youden)
    if return_coordinate:
        return youden[index], (fp[index], tp[index])
    else:
        return youden[index]


def _make_cv_subdir(basedir, i):
    """Return cross-validation subdirectory."""
    return os.path.join(basedir, str(i))


def _make_cv_filename(out_dir, basename, group_type, i,
                      out_ext=OUT_CSV_EXT_DEF):
    """Return cross-validation filename for CSV file."""
    return os.path.join(_make_cv_subdir(out_dir, i),
                        "{!s}_{!s}_{:d}{!s}".format(basename, group_type, i,
                                                    out_ext))


def _make_csv_basename(filename):
    """Return basename of CSV file."""
    basename = os.path.splitext(os.path.basename(filename))[0]
    if basename.endswith(".csv"):
        basename = os.path.splitext(basename)[0]
    return basename
