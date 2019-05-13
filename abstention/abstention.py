from __future__ import division, print_function, absolute_import
import numpy as np
from sklearn.metrics import roc_auc_score
from scipy.stats import entropy
import scipy.signal
#from sklearn.metrics import average_precision_score
import sys
from .calibration import map_to_softmax_format_if_appropriate


def basic_average_precision_score(y_true, y_score):
    y_true = y_true.squeeze()
    #sort by y_score
    sorted_y_true, sorted_y_score = zip(*sorted(zip(y_true, y_score),
                                                 key=lambda x: x[1]))
    sorted_y_true = np.array(sorted_y_true).astype("float64")
    num_pos = np.sum(sorted_y_true)
    num_neg = np.sum(1-sorted_y_true)
    num_pos_above = num_pos - np.cumsum(sorted_y_true)
    num_neg_above = num_neg - np.cumsum(1-sorted_y_true)
    num_pos_above[-1] = 1.0
    num_neg_above[-1] = 0.0
    precisions = num_pos_above/(num_pos_above+num_neg_above).astype("float64")
    average_precision = np.sum(sorted_y_true*precisions)/(num_pos)
    return average_precision

average_precision_score = basic_average_precision_score


class AbstentionEval(object):

    def __init__(self, metric, proportion_to_retain):
        self.metric = metric
        self.proportion_to_retain = proportion_to_retain

    def __call__(self, abstention_scores, y_true, y_score):
        #lower abstention score means KEEP
        indices = np.argsort(abstention_scores)[
                    :int(np.ceil(len(y_true)*self.proportion_to_retain))] 
        return self.metric(y_true=y_true[indices],
                           y_score=y_score[indices])


class AuPrcAbstentionEval(AbstentionEval):

    def __init__(self, proportion_to_retain):
        super(AuPrcAbstentionEval, self).__init__(
            metric=average_precision_score,
            proportion_to_retain=proportion_to_retain)


class AuRocAbstentionEval(AbstentionEval):

    def __init__(self, proportion_to_retain):
        super(AuRocAbstentionEval, self).__init__(
            metric=roc_auc_score,
            proportion_to_retain=proportion_to_retain)
    

class ThresholdFinder(object):

    def __call__(self, valid_labels, valid_posterior):
        raise NotImplementedError()


class FixedThreshold(ThresholdFinder):

    def __init__(self, threshold):
        self.threshold = threshold

    def __call__(self, valid_labels, valid_posterior):
        return self.threshold


class OptimalF1(ThresholdFinder):

    def __init__(self, beta,
                       range_to_search=np.arange(0.00, 1.00, 0.01),
                       verbose=True):
        self.beta = beta
        self.range_to_search = range_to_search
        self.verbose = verbose

    def __call__(self, valid_labels, valid_posterior):

        valid_labels = np.array(valid_labels) 
        total_positives = np.sum(valid_labels==1)

        best_score = -1
        best_threshold = 0
        for threshold in self.range_to_search:
            y_pred = np.array(valid_posterior > threshold)
            true_positives = np.sum(valid_labels*y_pred)
            predicted_positives = np.sum(y_pred)
            precision = float(true_positives)/\
                        (predicted_positives + np.finfo(np.float32).eps)
            recall = float(true_positives)/\
                        (total_positives + np.finfo(np.float32).eps)
            bb = self.beta ** 2
            score = ((1 + bb) * (precision * recall)) /\
                    (bb * precision + recall + np.finfo(np.float32).eps)
            if score > best_score:
                best_threshold = threshold
                best_score = score   
        if (self.verbose):
            print("Threshold is",best_threshold)
            sys.stdout.flush()
        return best_threshold 


class AbstainerFactory(object):

    def __call__(self, valid_labels,
                       valid_posterior,
                       valid_uncert,
                       train_embeddings,
                       train_labels):
        """
            Inputs: validation set labels, posterior probs, uncertainties
            Returns: a function that accepts posterior probs and
                        uncertainties and outputs the abstention scores,
                        where a low score = KEEP
        """
        raise NotImplementedError()


class MulticlassWrapper(AbstainerFactory):

    def __init__(self, single_class_abstainer_factory, verbose=True):
        self.single_class_abstainer_factory = single_class_abstainer_factory
        self.verbose = verbose

    def __call__(self, valid_labels,
                       valid_posterior,
                       valid_uncert,
                       train_embeddings,
                       train_labels):

        all_class_abstainers = []
        for class_idx in range(valid_labels.shape[1]):

            if (valid_labels is not None):
                class_valid_labels = valid_labels[:, class_idx] 
            else:
                class_valid_labels = None 

            if (valid_posterior is not None):
                class_valid_posterior = valid_posterior[:, class_idx]
            else:
                class_valid_posterior = None

            if (valid_uncert is not None):
                class_valid_uncert = valid_uncert[:, class_idx]
            else:
                class_valid_uncert = None

            if (train_embeddings is not None):
                class_train_embeddings = train_embeddings[:, class_idx]
            else:
                class_train_embeddings = None

            if (train_labels is not None):
                class_train_labels = train_labels[:, class_idx]
            else:
                class_train_labels = None
           
            class_abstainer = self.single_class_abstainer_factory(
                                    valid_labels=class_valid_labels,
                                    valid_posterior=class_valid_posterior,
                                    train_embeddings=class_train_embeddings,
                                    train_labels=class_train_labels) 
            all_class_abstainers.append(class_abstainer)

        def func(posterior_probs, uncertainties):

            all_class_scores = []

            for class_idx in range(posterior_probs.shape[1]):

                if (posterior_probs is not None):
                    class_posterior_probs = posterior_probs[:,class_idx]
                else:
                    class_posterior_probs = None

                if (uncertainties is not None):
                    class_uncertainties = uncertainties[:, class_idx]
                else:
                    class_uncertainties = None

                class_scores = all_class_abstainers[class_idx](
                                 posterior_probs=class_posterior_probs,
                                 uncertainties=class_uncertainties)
                all_class_scores.append(class_scores)
            return np.array(all_class_scores).transpose((1,0))

        return func
            

class RandomAbstention(AbstainerFactory):

    def __call__(self, valid_labels=None, valid_posterior=None,
                       valid_uncert=None, train_embeddings=None,
                       train_labels=None):

        def random_func(posterior_probs, uncertainties=None, embeddings=None):
            return np.random.permutation(range(len(posterior_probs)))/(
                     len(posterior_probs))
        return random_func


class NegPosteriorDistanceFromThreshold(AbstainerFactory):

    def __init__(self, threshold_finder):
        self.threshold_finder = threshold_finder

    def __call__(self, valid_labels, valid_posterior,
                       valid_uncert=None, train_embeddings=None,
                       train_labels=None):

        threshold = self.threshold_finder(valid_labels, valid_posterior)

        def abstaining_func(posterior_probs,
                            uncertainties=None, embeddings=None):
            return -np.abs(posterior_probs-threshold) 
        return abstaining_func


#Based on
# Fumera, Giorgio, Fabio Roli, and Giorgio Giacinto.
# "Reject option with multiple thresholds."
# Pattern recognition 33.12 (2000): 2099-2101.
class DualThresholdsFromPointFiveOnValidSet(AbstainerFactory):

    def __init__(self, fracs_to_abstain_on, metric):
        #fracts to abstain on = abstention fractions to consder when
        # returning rankings
        self.fracs_to_abstain_on = fracs_to_abstain_on
        self.metric = metric

    def __call__(self, valid_labels, valid_posterior,
                       valid_uncert=None, train_embeddings=None,
                       train_labels=None):

        def abstaining_func(posterior_probs,
                            uncertainties=None,
                            embeddings=None):
            sorted_posterior_probs = sorted(posterior_probs)
            idx_of_point_five = np.searchsorted(a=sorted_posterior_probs,
                                                v=0.5) 
            print("percentile point five",
                  idx_of_point_five/len(posterior_probs))

            abstention_thresholds = []
            for frac_to_abstain in self.fracs_to_abstain_on:
                num_to_abstain_on = int(len(posterior_probs)
                                        *frac_to_abstain)
                thresh_plus_perf = []
                for left_offset in range(0, num_to_abstain_on):
                    left_idx = idx_of_point_five-left_offset
                    right_idx = min(left_idx + num_to_abstain_on,
                                    len(posterior_probs)-1)
                    left_thresh = sorted_posterior_probs[left_idx]
                    right_thresh = sorted_posterior_probs[right_idx]
                   
                    (subset_valid_labels, subset_valid_posterior) =\
                      zip(*[x for x in
                            zip(valid_labels, valid_posterior)
                            if ((x[1] < left_thresh)
                                or (x[1] > right_thresh))])
                    perf = self.metric(y_true=subset_valid_labels,
                                       y_score=subset_valid_posterior) 
                    thresh_plus_perf.append(
                     ((left_thresh, right_thresh), perf))
                ((best_left_thresh, best_right_thresh), perf) =\
                    max(thresh_plus_perf, key=lambda x: x[1]) 
                abstention_thresholds.append((best_left_thresh,
                                              best_right_thresh))

            print(abstention_thresholds)
            abstention_scores = []
            for posterior_prob in posterior_probs:
                score = 0 
                for (left_thresh, right_thresh) in abstention_thresholds:
                    if ((posterior_prob >= left_thresh) and
                        (posterior_prob <= right_thresh)):
                        score += 1 
                abstention_scores.append(score)
            return np.array(abstention_scores)
        return abstaining_func 


class DistMaxClassProbFromOne(AbstainerFactory):

    def __call__(self, valid_labels=None, valid_posterior=None,
                       valid_uncert=None, train_embeddings=None,
                       train_labels=None):
        def abstaining_func(posterior_probs,
                            uncertainties=None,
                            embeddings=None):
            assert len(posterior_probs.shape)==2
            return 1-np.max(posterior_probs, axis=1)
        return abstaining_func


class Entropy(AbstainerFactory):

    def __call__(self, valid_labels=None, valid_posterior=None,
                       valid_uncert=None, train_embeddings=None,
                       train_labels=None):
        def abstaining_func(posterior_probs,
                            uncertainties=None,
                            embeddings=None):
            assert len(posterior_probs.shape)==2
            return -np.sum(posterior_probs*np.log(posterior_probs),axis=1)
        return abstaining_func


#Jenson-Shannon divergence from class freqs
class OneMinusJSDivFromClassFreq(AbstainerFactory):

    def __call__(self, valid_labels=None, valid_posterior=None,
                       valid_uncert=None, train_embeddings=None,
                       train_labels=None):
        #softmax_valid_labels =\
        #    map_to_softmax_format_if_appropriate(values=valid_labels)
        #mean_class_freqs = np.mean(softmax_valid_labels, axis=0)
        def abstaining_func(posterior_probs,
                            uncertainties=None,
                            embeddings=None):
            softmax_posterior_probs =\
                map_to_softmax_format_if_appropriate(values=posterior_probs) 
            mean_class_freqs = np.mean(softmax_posterior_probs, axis=0)
            assert len(softmax_posterior_probs.shape)==2
            M = 0.5*(mean_class_freqs[None,:] + softmax_posterior_probs)  
            jsd = (np.array([(0.5*entropy(pk=pk, qk=m)
                              + 0.5*entropy(pk=mean_class_freqs, qk=m))
                             for (m,pk) in zip(M, softmax_posterior_probs)]))
            return 1-jsd
        return abstaining_func


def get_weighted_kappa_predictions(predprobs, weights, mode):

    assert mode in ['argmax', 'optim', 'optim-num', 'optim-num-by-denom']

    expected_true_label_props = np.mean(predprobs, axis=0) 
    denominator_addition = np.sum(
        expected_true_label_props[None,:]*weights,axis=-1)
    numerator_addition = np.sum(predprobs[:,None,:]*weights[None,:,:],
                                axis=-1)
    if (mode=='argmax'):
        return np.argmax(predprobs, axis=-1) 
    elif (mode=='optim-num'):
        return np.argmin(numerator_addition,axis=-1)
    elif (mode=='optim-num-by-denom'):
        return np.argmin(numerator_addition/denominator_addition[None,:],
                         axis=-1)
    elif (mode=='optim'):
        #get an estimated value for the numerator and denominator according
        # to the optim-num-by-denom criterior
        standin_preds =  np.argmin(numerator_addition/
                                   denominator_addition[None,:],axis=-1)
        iterations = 5
        best_est_wkappa = None
        best_iter = None
        best_standin_preds = standin_preds
        for iter_num in range(iterations):
            estim_num = np.sum(numerator_addition[
                                list(range(len(standin_preds))),standin_preds])
            estim_denom = np.sum([denominator_addition[x]
                                  for x in standin_preds])
            standin_preds = np.argmin((numerator_addition+estim_num)/
                            (denominator_addition+estim_denom),axis=-1) 
            est_wkappa = (1-(estim_num/estim_denom))
            if (best_est_wkappa is None or best_est_wkappa < est_wkappa):
                best_standin_preds = standin_preds
                best_est_wkappa = est_wkappa
                best_iter = iter_num
            else:
                break
        return best_standin_preds 
    else:
        raise RuntimeError()


def weighted_kappa_metric(predprobs, true_labels, weights,
                          mode):
    #weights: axis 0 is prediction, axis 1 is true
    assert predprobs.shape[1]==weights.shape[1]
    assert predprobs.shape[1]==weights.shape[0]
    assert true_labels.shape[1]==weights.shape[1]
    assert true_labels.shape[1]==weights.shape[0]
    assert all([weights[i,i]==0 for i in range(weights.shape[1])])
    actual_class_proportions = np.mean(true_labels, axis=0)
    predictions = get_weighted_kappa_predictions(predprobs=predprobs,
                                                 weights=weights, mode=mode) 
    pred_class_proportions = np.array([
        np.mean(predictions==i)
        for i in range(predprobs.shape[1])])
    expected_confusion_matrix = (
        pred_class_proportions[:,None]*
        actual_class_proportions[None,:])
    denominator = np.sum(expected_confusion_matrix*weights)
    numerator = (np.sum([np.sum(weights[x]*y)
                         for (x,y) in zip(predictions,true_labels)])/
                     float(len(predictions)))
    return 1 - numerator/denominator
         

class WeightedKappa(AbstainerFactory):

    def __init__(self, weights, mode, estimate_class_imbalance_from_valid=False,
                       verbose=True):
        self.weights = weights
        self.mode=mode
        self.estimate_class_imbalance_from_valid =\
            estimate_class_imbalance_from_valid
        self.verbose = verbose

    def __call__(self, valid_labels=None, valid_posterior=None,
                       valid_uncert=None, train_embeddings=None,
                       train_labels=None):

        #one-hot encoded validation labels expected
        if (self.estimate_class_imbalance_from_valid):
            assert valid_labels is not None
            assert valid_posterior is not None
            assert np.max(valid_labels)==1.0
            assert valid_labels.shape[1]==self.weights.shape[1]
            assert valid_labels.shape[1]==self.weights.shape[0]
            assert valid_posterior.shape[1]==self.weights.shape[1]
            assert valid_posterior.shape[1]==self.weights.shape[0]
            valid_label_fractions =(
                np.sum(valid_labels,axis=0)/float(valid_labels.shape[0]))
            if (self.verbose):
                print("validation set weighted kappa", 
                       weighted_kappa_metric(
                        predprobs=valid_posterior,
                        true_labels=valid_labels,
                        weights=self.weights, mode=self.mode))
                print("validation set estimated weighted kappa from probs", 
                       weighted_kappa_metric(
                        predprobs=valid_posterior,
                        true_labels=valid_posterior,
                    weights=self.weights, mode=self.mode))

        def abstaining_func(posterior_probs,
                            uncertainties=None,
                            embeddings=None):
            assert posterior_probs.shape[1]==self.weights.shape[1]
            assert posterior_probs.shape[1]==self.weights.shape[0]
            est_label_numbers = (valid_label_fractions*len(posterior_probs) 
              if self.estimate_class_imbalance_from_valid
              else np.sum(posterior_probs,axis=0))
            predictions = get_weighted_kappa_predictions(
                predprobs=posterior_probs, weights=self.weights,
                mode=self.mode) 
            pred_class_numbers = np.array([
                np.sum(predictions==i)
                for i in range(posterior_probs.shape[1])])
            expected_confusion_matrix = (
                (pred_class_numbers[:,None]/float(len(posterior_probs)))*
                est_label_numbers[None,:])
            est_denominator = np.sum(expected_confusion_matrix*self.weights)
            est_numerator = np.sum([np.sum(self.weights[x]*y)
                             for (x,y) in zip(predictions,
                                              posterior_probs)])
            est_kappa = (1 - (est_denominator/est_numerator))
            #compute the difference abtaining with each example 
            expected_impact_abstentions = []
            for example_pred_class,example in zip(predictions,posterior_probs):
                new_est_kappa = 0
                #iterate over each possible label class
                for (label_class_idx,label_class_prob) in enumerate(example):
                    new_pred_class_numbers = np.array(pred_class_numbers) 
                    new_pred_class_numbers[example_pred_class] -= 1
                    new_est_label_numbers = np.array(est_label_numbers)
                    new_est_label_numbers[label_class_idx] -= 1 
                    new_expected_confusion_matrix = (
                        (new_pred_class_numbers[:,None]/
                         float(len(posterior_probs)-1))*
                        new_est_label_numbers[None,:])
                    new_est_denominator = np.sum(
                        new_expected_confusion_matrix*self.weights)
                    new_est_numerator = (est_numerator
                      - self.weights[example_pred_class,label_class_idx]) 
                    new_est_kappa += label_class_prob*(1 - 
                     (new_est_numerator/new_est_denominator))
                expected_impact_abstentions.append(new_est_kappa - est_kappa) 
            expected_impact_abstentions = np.array(expected_impact_abstentions) 
            return expected_impact_abstentions
        return abstaining_func


class NegativeAbsLogLikelihoodRatio(AbstainerFactory):

    def __call__(self, valid_labels, valid_posterior,
                       valid_uncert=None, train_embeddings=None,
                       train_labels=None):

        p_pos = np.sum(valid_labels)/len(valid_labels)
        assert p_pos > 0 and p_pos < 1.0, "only one class in labels"
        #lpr = log posterior ratio
        lpr = np.log(p_pos) - np.log(1-p_pos)

        def abstaining_func(posterior_probs,
                            uncertainties=None,
                            embeddings=None):
            #llr = log-likelihood ratio
            # prob = 1/(1 + e^-(llr + lpr))
            # (1+e^-(llr + lpr)) = 1/prob
            # e^-(llr + lpr) = 1/prob - 1
            # llr + lpr = -np.log(1/prob - 1)
            # llr = -np.log(1/prob - 1) - lpr
            np.clip(posterior_probs, a_min=1e-7, a_max=None, out=posterior_probs)
            llr = -np.log(1/(posterior_probs) - 1) - lpr
            return -np.abs(llr)
        return abstaining_func


class RecursiveMarginalDeltaMetric(AbstainerFactory):

    def __init__(self, proportion_to_retain):
        self.proportion_to_retain = proportion_to_retain

    def estimate_metric(self, ppos, pos_cdfs, neg_cdfs):
        raise NotImplementedError()

    def compute_metric(self, y_true, y_score):
        raise NotImplementedError()

    def compute_abstention_score(self, est_metric, ppos, pos_cdf, neg_cdf,
                                       est_numpos, est_numneg):
        raise NotImplementedError()

    def __call__(self, valid_labels=None,
                       valid_posterior=None,
                       valid_uncert=None,
                       train_embeddings=None,
                       train_labels=None):

        def abstaining_func(posterior_probs,
                            uncertainties=None,
                            embeddings=None):
            reverse_eviction_ordering = np.zeros(len(posterior_probs))
            #test_posterior_and_index have 2-tuples of prob, testing index
            test_posterior_and_index = [(x[1], x[0]) for x in
                                        enumerate(posterior_probs)]
            test_sorted_indices, test_sorted_posterior_probs =\
                zip(*sorted(enumerate(posterior_probs),
                      key=lambda x: x[1]))
            test_sorted_posterior_probs =\
                np.array(test_sorted_posterior_probs)

            items_remaining = len(posterior_probs)  
            while items_remaining >\
                  int(self.proportion_to_retain*len(posterior_probs)):
                if (items_remaining%100 == 0):
                    print("Items recursively evicted:",
                      (len(posterior_probs)-items_remaining),
                       "of",len(posterior_probs)-
                       int(self.proportion_to_retain*len(posterior_probs)))
                    sys.stdout.flush()
                est_numpos_from_data = np.sum(test_sorted_posterior_probs)
                est_numneg_from_data = np.sum(1-test_sorted_posterior_probs)
                est_pos_cdfs_from_data =\
                    (np.cumsum(test_sorted_posterior_probs))/\
                    est_numpos_from_data
                est_neg_cdfs_from_data =\
                    (np.cumsum(1-test_sorted_posterior_probs))/\
                    est_numneg_from_data
                est_metric_from_data=self.estimate_metric(
                    ppos=test_sorted_posterior_probs,
                    pos_cdfs=est_pos_cdfs_from_data,
                    neg_cdfs=est_neg_cdfs_from_data)

                test_sorted_abstention_scores = self.compute_abstention_score(
                    est_metric=est_metric_from_data,
                    est_numpos=est_numpos_from_data,
                    est_numneg=est_numneg_from_data,
                    ppos=test_sorted_posterior_probs,
                    pos_cdfs=est_pos_cdfs_from_data,
                    neg_cdfs=est_neg_cdfs_from_data)
                to_evict_idx = max(zip(test_sorted_indices,
                                       test_sorted_abstention_scores),
                                   key=lambda x: x[1])[0]
                reverse_eviction_ordering[to_evict_idx] = items_remaining  
                items_remaining -= 1
                idx_to_evict_from_sorted =\
                    np.argmax(test_sorted_abstention_scores)
                test_sorted_indices =\
                    np.array(list(test_sorted_indices[:
                                   idx_to_evict_from_sorted])
                           +list(test_sorted_indices[
                                   idx_to_evict_from_sorted+1:]))
                test_sorted_posterior_probs =\
                    np.array(list(test_sorted_posterior_probs[:
                                   idx_to_evict_from_sorted])
                         +list(test_sorted_posterior_probs[
                                   idx_to_evict_from_sorted+1:]))
            return reverse_eviction_ordering

        return abstaining_func


class MarginalDeltaMetric(AbstainerFactory):

    def __init__(self, estimate_cdfs_from_valid=False,
                       estimate_imbalance_and_perf_from_valid=False,
                       all_estimates_from_valid=False,
                       verbose=False):
        self.all_estimates_from_valid = all_estimates_from_valid
        if (self.all_estimates_from_valid):
            estimate_cdfs_from_valid = True
            estimate_imbalance_and_perf_from_valid = True
        self.estimate_cdfs_from_valid = estimate_cdfs_from_valid
        self.estimate_imbalance_and_perf_from_valid =\
             estimate_imbalance_and_perf_from_valid
        self.verbose = verbose

    def estimate_metric(self, ppos, pos_cdfs, neg_cdfs):
        raise NotImplementedError()

    def compute_metric(self, y_true, y_score):
        raise NotImplementedError()

    def compute_abstention_score(self, est_metric, ppos, pos_cdf, neg_cdf,
                                       est_numpos, est_numneg):
        raise NotImplementedError()

    def __call__(self, valid_labels, valid_posterior,
                       valid_uncert=None, train_embeddings=None,
                       train_labels=None):

        if (self.all_estimates_from_valid):
            print("Estimating everything relative to validation set")

        if (self.estimate_cdfs_from_valid
            or self.estimate_imbalance_and_perf_from_valid): 
            #get the original auROC from the validation set
            valid_est_metric = np.array(self.compute_metric(
                                             y_true=valid_labels,
                                             y_score=valid_posterior))
            valid_num_positives = np.sum(valid_labels==1)
            valid_num_negatives = np.sum(valid_labels==0)

            #compute the cdf for the positives and the negatives from valid set
            sorted_labels_and_probs = sorted(
                zip(valid_labels, valid_posterior), key=lambda x: x[1]) 
            running_sum_positives = [0]
            running_sum_negatives = [0]
            for label, prob in sorted_labels_and_probs:
                if (label==1):
                    running_sum_positives.append(running_sum_positives[-1]+1)
                    running_sum_negatives.append(running_sum_negatives[-1])
                else:
                    running_sum_positives.append(running_sum_positives[-1])
                    running_sum_negatives.append(running_sum_negatives[-1]+1)
            valid_positives_cdf =\
                np.array(running_sum_positives)/float(valid_num_positives) 
            valid_negatives_cdf =\
                np.array(running_sum_negatives)/float(valid_num_negatives) 

            #validation_vals are a 3-tuple of prob, positive_cdf, neg_cdf
            validation_vals = list(zip([x[1] for x in sorted_labels_and_probs],
                                   valid_positives_cdf, valid_negatives_cdf))
        else:
            valid_est_metric = None


        def abstaining_func(posterior_probs,
                            uncertainties=None,
                            embeddings=None):
            #test_posterior_and_index have 2-tuples of prob, testing index
            test_posterior_and_index = [(x[1], x[0]) for x in
                                        enumerate(posterior_probs)]
            if (self.estimate_cdfs_from_valid
                or self.estimate_imbalance_and_perf_from_valid): 
                sorted_valid_and_test =\
                    sorted(validation_vals+test_posterior_and_index,
                           key=lambda x: x[0])
                pos_cdf = 0
                neg_cdf = np.finfo(np.float32).eps
                test_sorted_posterior_probs = []
                test_sorted_pos_cdfs = []
                test_sorted_neg_cdfs = []
                test_sorted_indices = []
                to_return = np.zeros(len(posterior_probs))
                for value in sorted_valid_and_test:
                    is_from_valid = True if len(value)==3 else False 
                    if (is_from_valid):
                        pos_cdf = value[1]
                        neg_cdf = max(value[2],np.finfo(np.float32).eps)
                    else:
                        ppos = value[0]
                        idx = value[1]
                        test_sorted_posterior_probs.append(ppos)
                        test_sorted_indices.append(idx)
                        test_sorted_pos_cdfs.append(pos_cdf)
                        test_sorted_neg_cdfs.append(neg_cdf)
                test_sorted_posterior_probs =\
                    np.array(test_sorted_posterior_probs)
                test_sorted_pos_cdfs = np.array(test_sorted_pos_cdfs)
                test_sorted_neg_cdfs = np.array(test_sorted_neg_cdfs)

                valid_frac_pos = valid_num_positives/\
                                 (valid_num_positives+valid_num_negatives)
                valid_frac_neg = valid_num_negatives/\
                                 (valid_num_positives+valid_num_negatives)
                if (self.all_estimates_from_valid):
                    est_numpos_from_valid = valid_num_positives
                    est_numneg_from_valid = valid_num_negatives
                else:
                    est_numpos_from_valid = valid_frac_pos*len(posterior_probs)
                    est_numneg_from_valid = valid_frac_neg*len(posterior_probs)
            else:
                (test_sorted_indices,
                 test_sorted_posterior_probs) = [np.array(x) for x in zip(*
                  sorted(enumerate(posterior_probs),
                         key=lambda x: x[1]))]
                est_numpos_from_valid = None
                est_numneg_from_valid = None
                test_sorted_pos_cdfs = None
                test_sorted_neg_cdfs = None
                
             
            est_numpos_from_data = np.sum(test_sorted_posterior_probs)
            est_numneg_from_data = np.sum(1-test_sorted_posterior_probs)
            est_pos_cdfs_from_data =\
                (np.cumsum(test_sorted_posterior_probs))/est_numpos_from_data
            est_neg_cdfs_from_data =\
                (np.cumsum(1-test_sorted_posterior_probs))/est_numneg_from_data

            if (self.estimate_cdfs_from_valid):
                est_metric_from_data=self.estimate_metric(
                    ppos=test_sorted_posterior_probs,
                    pos_cdfs=test_sorted_pos_cdfs,
                    neg_cdfs=test_sorted_neg_cdfs)
            else:
                est_metric_from_data=self.estimate_metric(
                    ppos=test_sorted_posterior_probs,
                    pos_cdfs=est_pos_cdfs_from_data,
                    neg_cdfs=est_neg_cdfs_from_data)

            if (self.verbose):
                print("data est metric", est_metric_from_data)
                sys.stdout.flush()

            test_sorted_abstention_scores = self.compute_abstention_score(
                est_metric=(valid_est_metric if
                            self.estimate_imbalance_and_perf_from_valid
                            else est_metric_from_data),
                est_numpos=(est_numpos_from_valid if
                            self.estimate_imbalance_and_perf_from_valid
                            else est_numpos_from_data),
                est_numneg=(est_numneg_from_valid if
                            self.estimate_imbalance_and_perf_from_valid else
                            est_numneg_from_data),
                ppos=np.array(test_sorted_posterior_probs),
                pos_cdfs=(np.array(test_sorted_pos_cdfs)
                          if self.estimate_cdfs_from_valid
                          else est_pos_cdfs_from_data),
                neg_cdfs=(np.array(test_sorted_neg_cdfs)
                          if self.estimate_cdfs_from_valid
                          else est_neg_cdfs_from_data)
            )

            final_abstention_scores = np.zeros(len(posterior_probs)) 
            final_abstention_scores[test_sorted_indices] =\
                test_sorted_abstention_scores 
            return final_abstention_scores

        return abstaining_func


class AbstractMarginalDeltaMetricMixin(object):

    def estimate_metric(self, ppos, pos_cdfs, neg_cdfs):
        raise NotImplementedError()

    def compute_metric(self, y_true, y_score):
        raise NotImplementedError()

    def compute_abstention_score(self, est_metric, est_numpos, est_numneg,
                                       ppos, pos_cdfs, neg_cdfs):
        raise NotImplementedError()


class MarginalDeltaAuRocMixin(AbstractMarginalDeltaMetricMixin):

    def estimate_metric(self, ppos, pos_cdfs, neg_cdfs): 
        #probability that a randomly chosen positive is ranked above
        #a randomly chosen negative:
        est_total_positives = np.sum(ppos)
        #probability of being ranked above a randomly chosen negative
        #is just neg_cdf
        return np.sum(ppos*neg_cdfs)/est_total_positives

    def compute_metric(self, y_true, y_score):
        return roc_auc_score(y_true=y_true, y_score=y_score)

    def compute_abstention_score(self, est_metric, est_numpos, est_numneg,
                                       ppos, pos_cdfs, neg_cdfs):
        return (ppos*((est_metric - neg_cdfs)/(est_numpos-1)) 
                + (1-ppos)*((est_metric - (1-pos_cdfs))/(est_numneg-1)))


class MarginalDeltaAuRoc(MarginalDeltaAuRocMixin, MarginalDeltaMetric):
    pass


class RecursiveMarginalDeltaAuRoc(MarginalDeltaAuRocMixin,
                                  RecursiveMarginalDeltaMetric):
    pass


class MarginalDeltaAuPrcMixin(AbstractMarginalDeltaMetricMixin):

    def estimate_metric(self, ppos, pos_cdfs, neg_cdfs): 
        #average precision over all the positives
        num_pos = np.sum(ppos)
        num_neg = np.sum(1-ppos)
        #num positives ranked above = (1-pos_cdfs)*num_pos
        #num negatives ranked above = (1-neg_cdfs)*num_neg
        pos_cdfs[-1] = np.finfo(np.float32).eps #prevent div by 0
        precision_at_threshold = ((1-pos_cdfs)*num_pos)/\
                                 ((1-pos_cdfs)*num_pos + (1-neg_cdfs)*num_neg)
        precision_at_threshold[-1] = 1.0
        return np.sum(ppos*precision_at_threshold)/num_pos

    def compute_metric(self, y_true, y_score):
        return average_precision_score(y_true=y_true, y_score=y_score)

    def compute_abstention_score(self, est_metric, est_numpos, est_numneg,
                                       ppos, pos_cdfs, neg_cdfs):
        est_nneg_above = est_numneg*(1-neg_cdfs)
        est_npos_above = est_numpos*(1-pos_cdfs)
        #to prevent 0/0:
        est_npos_above[-1] = 1.0
        est_nneg_above[-1] = 0.0
        precision_at_threshold = est_npos_above/(est_npos_above
                                                 + est_nneg_above)
        #mcpr is marginal change in precision at this threshold due
        # to abstaining on an example at a higher threshold
        num_examples_above = (est_npos_above + est_nneg_above)
        mcpr_denom = np.maximum(num_examples_above - 1, 1) #avoid explosion
        #mcpr_term1 is what happens if the example abstained on is a negative
        mcpr_term1 = precision_at_threshold/mcpr_denom
        #(mcpr_term1 + mcpr_term2) is what happens if the example abstain on  
        # is a positive
        mcpr_term2 = -1.0/mcpr_denom
        #weighting by ppos is because only positives contribute to average
        # precision score
        #Need to subtract ppos*mcpr_termX because cumsum is inclusive
        cmcpr_term1 = np.cumsum(ppos*mcpr_term1) - ppos*mcpr_term1
        cmcpr_term2 = np.cumsum(ppos*mcpr_term2) - ppos*mcpr_term2
        #compute the delta if evicted example is a positive
        delta_if_positive = ((est_metric - precision_at_threshold)
                             + (cmcpr_term1 + cmcpr_term2))/(est_numpos-1) 
        delta_if_negative = cmcpr_term1/est_numpos 
        slope = ppos*delta_if_positive + (1-ppos)*delta_if_negative

        return slope



class MarginalDeltaAuPrc(MarginalDeltaAuPrcMixin, MarginalDeltaMetric):
    pass


class RecursiveMarginalDeltaAuPrc(MarginalDeltaAuPrcMixin,
                                  RecursiveMarginalDeltaMetric):
    pass


class Uncertainty(AbstainerFactory):

    def __call__(self, valid_labels=None, valid_posterior=None,
                       valid_uncert=None, train_embeddings=None,
                       train_labels=None):

        def abstaining_func(posterior_probs,
                            uncertainties,
                            embeddings=None):
            #posterior_probs can be None
            return uncertainties
        return abstaining_func


class CoreSetMinDist(AbstainerFactory):

    def __call__(self, train_embeddings, train_labels, valid_labels=None,
                       valid_posterior=None, valid_uncert=None):

        from sklearn.neighbors import NearestNeighbors
        nbrs = NearestNeighbors(n_neighbors=1).fit(train_embeddings)

        def abstaining_func(embeddings, posterior_probs=None,
                            uncertainties=None):
            #interrogate the KNN object with the provided embeddings
            #return the distance to the nearest
            distances, indices = nbrs.kneighbors(embeddings)
            distances = distances.squeeze()
            return distances #the larger the distance, the less confident
        return abstaining_func


class NNDist(AbstainerFactory):

    def __init__(self, k):
        self.k = k

    def __call__(self, train_embeddings, train_labels, valid_labels=None,
                       valid_posterior=None, valid_uncert=None):

        from sklearn.neighbors import NearestNeighbors
        nbrs = NearestNeighbors(n_neighbors=self.k).fit(train_embeddings)
        max_class = int(np.max(train_labels))
        
        def abstaining_func(embeddings, posterior_probs=None,
                            uncertainties=None):
            #interrogate the KNN object with the provided embeddings
            #for the k nearest neighbors
            #return the metric in the paper
            distances, indices = nbrs.kneighbors(embeddings)
            #exponentiate the distances
            distances = np.exp(np.array(distances)*-1)
            confidence_scores = []
            for ex_nn_distances, ex_nn_indices, prob in zip(distances,
                                                            indices,
                                                            posterior_probs):
                
                nn_labels = np.array([train_labels[idx] for
                                      idx in ex_nn_indices]).squeeze()
                denominator = sum(ex_nn_distances)
                
                class_confidences = []
                examples_accounted_for = 0
                for i in range(max_class+1):
                    class_distances = ex_nn_distances[nn_labels==i] 
                    examples_accounted_for += len(class_distances)
                    class_confidences.append(sum(class_distances)/denominator)
                #make sure all examples are accounted for
                assert len(nn_labels)==examples_accounted_for
                #let the confidence score be weighted by the posterior prob
                #take a weighted sum of the confidence across the classes
                # according to the posterior probability (I didn't find
                # this detail in the paper, so I am not totally sure how they
                # obtained a single confidence score in the end...)
                if (hasattr(prob, '__iter__')):
                    confidence_scores.append(sum(class_confidences*prob))
                else:
                    assert len(class_confidences)==2
                    confidence_scores.append(class_confidences[0]*(1-prob)
                                             + class_confidences[1]*prob)
            #the further from 1 you are, the less confident you are
            return 1-np.array(confidence_scores)
        return abstaining_func


class ConvexHybrid(AbstainerFactory):

    def __init__(self, factory1, factory2,
                       abstention_eval_func, stepsize=0.1,
                       verbose=True):
        self.factory1 = factory1
        self.factory2 = factory2
        self.abstention_eval_func = abstention_eval_func
        self.stepsize = stepsize
        self.verbose = verbose

    def __call__(self, valid_labels, valid_posterior,
                       valid_uncert, train_embeddings=None,
                       train_labels=None):

        factory1_func = self.factory1(valid_labels=valid_labels,
                                      valid_posterior=valid_posterior,
                                      valid_uncert=valid_uncert)
        factory2_func = self.factory2(valid_labels=valid_labels,
                                      valid_posterior=valid_posterior,
                                      valid_uncert=valid_uncert)

        def evaluation_func(scores):
            return self.abstention_eval_func(
                    abstention_scores=scores,
                    y_true=valid_labels,
                    y_score=valid_posterior)  

        a = find_best_mixing_coef(
                evaluation_func=evaluation_func,
                scores1=factory1_func(posterior_probs=valid_posterior,
                                      uncertainties=valid_uncert),
                scores2=factory2_func(posterior_probs=valid_posterior,
                                      uncertainties=valid_uncert),
                stepsize=self.stepsize)
       
        if (self.verbose):
            print("Best a",a) 

        def abstaining_func(posterior_probs,
                            uncertainties,
                            embeddings=None):
            scores1 = factory1_func(posterior_probs=posterior_probs,
                                    uncertainties=uncertainties)
            scores2 = factory2_func(posterior_probs=posterior_probs,
                                   uncertainties=uncertainties)
            return a*scores1 + (1-a)*scores2
        return abstaining_func


def find_best_mixing_coef(evaluation_func, scores1, scores2, stepsize):

    assert stepsize > 0.0 and stepsize < 1.0
    coefs_to_try = np.arange(0.0, 1+stepsize, stepsize)

    best_objective = None
    best_a = 0
    for a in coefs_to_try:
        b = 1.0 - a
        scores = a*scores1 + b*scores2
        objective = evaluation_func(scores) 
        if (best_objective is None or objective > best_objective):
            best_objective = objective 
            best_a = a
    return best_a


def get_sorted_probs_and_indices(posterior_probs):
    sorted_idx_and_probs = sorted(enumerate(posterior_probs),
                                      key=lambda x: x[1])
    indices = [x[0] for x in sorted_idx_and_probs]
    sorted_probs = np.array([x[1] for x in sorted_idx_and_probs])
    return (sorted_probs, indices)
  

def reorder_scores(unreordered_scores, indices):
    to_return = np.zeros(len(unreordered_scores))
    to_return[indices] = unreordered_scores
    return to_return
          
          
def pad_windowed_scores(signal, return_max_across_windows, window_size):

    if (return_max_across_windows):
        #return the maximum score across all windows that a particular
        # index falls in
        padded = np.array([-np.inf for i in range(window_size-1)]+
                          list(signal)
                          +[-np.inf for i in range(window_size-1)])
        rolling = np.lib.stride_tricks.as_strided(
            padded, shape=(len(signal)+(window_size-1),
                           window_size),
            strides=padded.strides + (padded.strides[-1],))
        return np.max(rolling,axis=-1)
    else:
        #return score when a window *centered* at a particular index
        # is abstained on
        left_pad = int((window_size-1)/2.0)
        right_pad = (window_size-1) - int((window_size-1)/2.0)
        return np.array([signal[0] for i in range(left_pad)]
                        + list(signal)
                        + [signal[-1] for i in range(right_pad)])


class MonteCarloSampler(AbstainerFactory):
    
    def __init__(self, n_samples, smoothing_window_size,
                       return_max_across_windows=True,
                       polyorder=1, seed=1):
        self.rng = np.random.RandomState(seed)
        self.n_samples = n_samples
        self.smoothing_window_size = smoothing_window_size
        self.polyorder = polyorder
        
    def sample(self, sorted_probs):
        true_labels = 1.0*(self.rng.rand(*sorted_probs.shape)
                           < sorted_probs)
        pos_cumsum = np.array([0]+list(np.cumsum(true_labels)))
        neg_cumsum = np.array([0]+list(np.cumsum(1-true_labels))) 
        return true_labels, pos_cumsum, neg_cumsum
   
    def postprocess_total_scores_marginalabst(self, total_scores, indices):
        mean_scores = total_scores/self.n_samples
        smoothed_mean_scores =\
            self.smooth_signal(signal=mean_scores)
        return reorder_scores(unreordered_scores=smoothed_mean_scores,
                              indices=indices)
    
    def smooth_signal(self, signal):
        if (self.smoothing_window_size is not None):
            return scipy.signal.savgol_filter(
                signal, window_length=self.smoothing_window_size,
                polyorder=self.polyorder, mode='nearest')
        else:
            return signal
      

class MonteCarloSamplerWindowAbst(MonteCarloSampler):  
     
    def __init__(self, num_to_abstain_on,
                       return_max_across_windows, **kwargs):
        self.num_to_abstain_on = num_to_abstain_on
        self.return_max_across_windows = return_max_across_windows
        super(MonteCarloSamplerWindowAbst, self).__init__(**kwargs)        
    
    def postprocess_total_scores_windowabst(self, total_scores, indices):
        mean_scores = total_scores/self.n_samples
        #total_scores has length of len(indices)+1-window_size
        #represents score when a window beginning at that index is abstained on
        window_size = self.num_to_abstain_on
        assert len(mean_scores) == (len(indices) + 1 - window_size)
        smoothed_scores = self.smooth_signal(signal=mean_scores)
        unreordered_scores = None   
        unreordered_scores = pad_windowed_scores(
            signal=smoothed_scores,
            return_max_across_windows=self.return_max_across_windows,
            window_size=window_size)
        return reorder_scores(unreordered_scores=unreordered_scores,
                              indices=indices)


class MonteCarloMarginalDeltaAuRoc(MonteCarloSampler):

    def __init__(self, n_samples, smoothing_window_size, **kwargs):
        super(MonteCarloMarginalDeltaAuRoc, self).__init__(
            n_samples=n_samples, smoothing_window_size=smoothing_window_size,
            **kwargs)

    def __call__(self, **kwargs):

        def abstaining_func(posterior_probs,
                            uncertainties=None,
                            embeddings=None):
            (sorted_probs, indices) = get_sorted_probs_and_indices(
                                        posterior_probs=posterior_probs)
            tot_auroc_deltas = np.zeros(len(sorted_probs))

            for sample_num in range(self.n_samples):
                labels, pos_cumsum, neg_cumsum = self.sample(
                    sorted_probs=sorted_probs) 
                
                tot_neg = neg_cumsum[-1]
                tot_pos = pos_cumsum[-1]

                frac_neg_lt = neg_cumsum/tot_neg  
                frac_pos_gte = (tot_pos-pos_cumsum)/tot_pos
                
                tot_fracneglt_positives = np.sum(
                    frac_neg_lt[:-1][labels==1.0])
                tot_fracposgt_negatives = np.sum(
                    frac_pos_gte[:-1][labels==0.0])
                
                delta_aurocs = np.zeros(len(labels)) 
                delta_aurocs[labels==1.0] =(
                    (tot_fracneglt_positives - frac_neg_lt[:-1][labels==1.0])/
                    (tot_pos-1)) - (tot_fracneglt_positives/tot_pos)
                delta_aurocs[labels==0.0] =(
                    (tot_fracposgt_negatives - frac_pos_gte[:-1][labels==0.0])/
                    (tot_neg-1)) - (tot_fracposgt_negatives/tot_neg)

                tot_auroc_deltas += delta_aurocs
            
            return self.postprocess_total_scores_marginalabst(
                total_scores=tot_auroc_deltas, indices=indices)

        return abstaining_func
      
 
class MonteCarloWindowAbstDeltaTprAtFprThreshold(MonteCarloSamplerWindowAbst):

    def __init__(self, n_samples, fpr_threshold, num_to_abstain_on,
                       return_max_across_windows,
                       smoothing_window_size, **kwargs):
        self.fpr_threshold = fpr_threshold
        super(MonteCarloWindowAbstDeltaTprAtFprThreshold, self).__init__(
            n_samples=n_samples, smoothing_window_size=smoothing_window_size,
            num_to_abstain_on=num_to_abstain_on,
            return_max_across_windows=return_max_across_windows,
            **kwargs)
    
    def __call__(self, **kwargs):
      
        def abstaining_func(posterior_probs, uncertainties=None,
                            embeddings=None):
            (sorted_probs, indices) = get_sorted_probs_and_indices(
                            posterior_probs=posterior_probs)
            window_size = self.num_to_abstain_on
            tot_tpr_deltas = np.zeros((len(sorted_probs)+1)-window_size)
            
            window_start_idx = np.arange((len(sorted_probs)+1)-window_size)
            window_end_idx = window_start_idx+window_size
            
            for sample_num in range(self.n_samples):
                labels, pos_cumsum, neg_cumsum = self.sample(
                    sorted_probs=sorted_probs)
                #identify the point of target tpr, with and without abstention 
                totpos = float(pos_cumsum[-1])
                totneg = float(neg_cumsum[-1])
                
                numneg_above = totneg - neg_cumsum
                numpos_above = totpos - pos_cumsum
                
                #when negatives to the right are abstained on, the
                # fpr gets better (i.e. decreases)
                #Figure out num of negatives to the right that need to
                # be evicted before given threshold dips below the target fpr
                negtoright_eviction_needed = np.ceil(
                    np.maximum(numneg_above - self.fpr_threshold*totneg,0)/(
                    1 - self.fpr_threshold)).astype(int)  
                
                #find the earliest index threshold that satisfies the fpr
                # requirement in the case of no eviction
                noevict_thresh = next(x[0]
                 for x in enumerate(negtoright_eviction_needed)
                 if x[1]==0)
                
                
                #iterate down to find the new threshold when a given num to
                # the right are evicted
                thresh_with_negrightevic = np.full(window_size+1, np.nan)
                curr_thresh = noevict_thresh
                for negrightevic in range(window_size+1):
                    #decrement the threshold has far as possible while
                    # still satisfying the fpr constraint
                    while (curr_thresh > 0 and
                           negtoright_eviction_needed[curr_thresh-1]
                           <= negrightevic):
                        curr_thresh = curr_thresh - 1
                    thresh_with_negrightevic[negrightevic] = curr_thresh
                assert thresh_with_negrightevic[0] == noevict_thresh
                
                #When negatives to the left are abstained on, fpr gets
                # worse (i.e. increases)
                #Figure out num of negatives to the left that could be
                # evicted while still allowing the given threshold to
                # satisfy the target fpr
                negtoleft_eviction_tolerable = np.floor(
                  np.maximum(self.fpr_threshold*totneg - numneg_above,0)/(
                  self.fpr_threshold)).astype(int)
                #iterate upwards to find the new threshold when a given num to
                # the left are evicted
                thresh_with_negleftevic = np.full(window_size+1, np.nan)
                curr_thresh = noevict_thresh
                for negleftevic in range(window_size+1):
                    #increment the threshold as needed until we satisfy
                    # the fpr constraint
                    while (curr_thresh < len(sorted_probs) and
                           negtoleft_eviction_tolerable[curr_thresh]
                           < negleftevic):
                        curr_thresh = curr_thresh + 1
                    thresh_with_negleftevic[negleftevic] = curr_thresh
                assert thresh_with_negleftevic[0] == noevict_thresh

                #compute the number of positives and negatives in each window
                npos_in_window = (pos_cumsum[window_size:] -
                                  pos_cumsum[:-window_size]).astype("int")
                nneg_in_window = window_size-npos_in_window
                
                #Figure out the threshold for each abstention window, based
                # on the number of negatives abstained on
                candidate_windowtoright_thresh =\
                  thresh_with_negrightevic[nneg_in_window]
                candidate_windowtoleft_thresh =\
                  thresh_with_negleftevic[nneg_in_window]
                
                thresh_after_window_abst = (
                   ((candidate_windowtoright_thresh <= window_start_idx)*
                     candidate_windowtoright_thresh)
                  +((candidate_windowtoright_thresh > window_start_idx)*
                     np.maximum(candidate_windowtoleft_thresh,
                                window_end_idx))).astype("int")
                tpr_after_window_abst = (
                  (numpos_above[thresh_after_window_abst]-
                   npos_in_window*(thresh_after_window_abst <= window_start_idx)
                  )/(totpos - npos_in_window))
                
                #print(set(tpr_after_window_abst))
                tot_tpr_deltas += tpr_after_window_abst - (
                                     numpos_above[noevict_thresh]/totpos)
                
            return self.postprocess_total_scores_windowabst(
                total_scores=tot_tpr_deltas, indices=indices)
        
        return abstaining_func
      
      
class MonteCarloMarginalDeltaTprAtFprThreshold(MonteCarloSampler):

    def __init__(self, n_samples, fpr_threshold,
                       smoothing_window_size, **kwargs):
        self.fpr_threshold = fpr_threshold
        super(MonteCarloMarginalDeltaTprAtFprThreshold, self).__init__(
            n_samples=n_samples, smoothing_window_size=smoothing_window_size,
            **kwargs)

    def __call__(self, **kwargs):

        def abstaining_func(posterior_probs,
                            uncertainties=None,
                            embeddings=None):
            (sorted_probs, indices) = get_sorted_probs_and_indices(
                            posterior_probs=posterior_probs)

            tr_vec = np.arange((len(sorted_probs)+1))
            tot_tpr_deltas = np.zeros(len(sorted_probs))

            for sample_num in range(self.n_samples):
                labels, pos_cumsum, neg_cumsum = self.sample(
                    sorted_probs=sorted_probs) 
                #identify the point of target tpr, with and without abstention 
                totpos = float(pos_cumsum[-1])
                totneg = float(neg_cumsum[-1])
                neg_above = totneg-neg_cumsum

                if (totpos > 0.0 and totneg > 0.0):
               
                    fpr_vec = ((totneg-neg_cumsum)/totneg)
                    fpr_negrightevict_vec = (neg_above-1.0)/(totneg-1.0) 
                    fpr_negleftevict_vec = neg_above/(totneg-1.0)

                    fpr_thresh = tr_vec[fpr_vec <= self.fpr_threshold][0]
                    fpr_negrightevict_thresh = tr_vec[fpr_negrightevict_vec <=
                                                   self.fpr_threshold][0] 
                    fpr_negleftevict_thresh = tr_vec[fpr_negleftevict_vec <=
                                                     self.fpr_threshold][0]
                    
                    right_of_thresh_mask = tr_vec[:-1] >= fpr_thresh
                    left_of_thresh_mask = right_of_thresh_mask==False
                    positives_mask = labels==1.0
                    negatives_mask = positives_mask==False                    
                    
                    tpr_at_thresh = 1.0 - (pos_cumsum[fpr_thresh]/totpos)
                    tpr_at_thresh_posrightevict =\
                        1.0 - (pos_cumsum[fpr_thresh]/(totpos-1.0))
                    tpr_at_thresh_posleftevict =\
                        1.0 - ((pos_cumsum[fpr_thresh]-1.0)/(totpos-1.0)) 
                    tpr_at_negrightevict_thresh =\
                        1.0 - (pos_cumsum[fpr_negrightevict_thresh]/totpos)
                    tpr_at_negleftevict_thresh =\
                        1.0 - (pos_cumsum[fpr_negleftevict_thresh]/totpos)
                    
                    assert (tpr_at_thresh >= tpr_at_thresh_posrightevict)
                    assert (tpr_at_thresh <= tpr_at_thresh_posleftevict)
                    assert (tpr_at_negrightevict_thresh >= tpr_at_thresh)
                    assert (tpr_at_negleftevict_thresh <= tpr_at_thresh)
                    
                    positives_right_of_thresh =\
                        positives_mask*(tr_vec[:-1] >= fpr_thresh)
                    positives_left_of_thresh =\
                        positives_mask*(tr_vec[:-1] < fpr_thresh)
                    negatives_right_of_thresh =\
                        negatives_mask*(tr_vec[:-1] >= fpr_negrightevict_thresh)
                    negatives_left_of_thresh =\
                        negatives_mask*(tr_vec[:-1] < fpr_negrightevict_thresh)
                    
                    #print(tpr_at_thresh_posleftevict,
                    #      tpr_at_thresh_posrightevict,
                    #      tpr_at_negleftevict_thresh,
                    #      tpr_at_negrightevict_thresh)
                    
                    tpr_deltas =\
                     (
                       (positives_left_of_thresh
                       *(tpr_at_thresh_posleftevict - tpr_at_thresh))
                      +(positives_right_of_thresh
                        *(tpr_at_thresh_posrightevict - tpr_at_thresh))
                      +(negatives_left_of_thresh*
                        (tpr_at_negleftevict_thresh - tpr_at_thresh))
                      +(negatives_right_of_thresh*
                        (tpr_at_negrightevict_thresh - tpr_at_thresh))
                     )
                    
                    tot_tpr_deltas += tpr_deltas     
            
            return self.postprocess_total_scores_marginalabst(
                total_scores=tot_tpr_deltas, indices=indices)

        return abstaining_func


class MonteCarloWindowAbstDeltaAuroc(MonteCarloSamplerWindowAbst):

    def __init__(self, n_samples, num_to_abstain_on,
                       return_max_across_windows,
                       smoothing_window_size, **kwargs):
        super(MonteCarloWindowAbstDeltaAuroc, self).__init__(
            n_samples=n_samples, smoothing_window_size=smoothing_window_size,
            num_to_abstain_on=num_to_abstain_on,
            return_max_across_windows=return_max_across_windows,
            **kwargs)
    
    def __call__(self, **kwargs):
      
        def abstaining_func(posterior_probs, uncertainties=None,
                            embeddings=None):
            (sorted_probs, indices) = get_sorted_probs_and_indices(
                            posterior_probs=posterior_probs)
            window_size = self.num_to_abstain_on
            tot_auroc_deltas = np.zeros((len(sorted_probs)+1)-window_size)       
            window_start_idx = np.arange((len(sorted_probs)+1)-window_size)
            window_end_idx = window_start_idx+window_size
            
            for sample_num in range(self.n_samples):
                labels, pos_cumsum, neg_cumsum = self.sample(
                    sorted_probs=sorted_probs)
                totpos = float(pos_cumsum[-1])
                totneg = float(neg_cumsum[-1])
                
                sum_negcumsum = np.sum(neg_cumsum[1:]*labels)
                curr_auroc = sum_negcumsum/(totneg*totpos)
                
                #compute the number of positives and negatives in each window
                npos_in_window = (pos_cumsum[window_size:] -
                                  pos_cumsum[:-window_size])
                nneg_in_window = window_size-npos_in_window
                #also compute the sum of neg_cumsum*labels in each window
                cumsum_negcumsum = np.array(
                    [0]+list(np.cumsum(neg_cumsum[1:]*labels)))
                negcumsum_in_window = (cumsum_negcumsum[window_size:] -
                                       cumsum_negcumsum[:-window_size])
                
                #Figure out how sum_negcumsum will be adjusted
                # if a given window is left out. Basically, at each positive
                # above the window, the neg_cumsum will be reduced by the
                # number of negatives within the window.
                numpos_above_window = totpos - pos_cumsum[window_size:]
                adj_sum_negcumsum = (sum_negcumsum - negcumsum_in_window -
                 numpos_above_window*nneg_in_window)

                #divide adj_sum_negcumsum by the adjusted totneg and totpos
                # to get the new auroc
                new_auroc = adj_sum_negcumsum/(
                  (totpos-npos_in_window)*(totneg-nneg_in_window))
                
                tot_auroc_deltas += new_auroc - curr_auroc                 
                
            return self.postprocess_total_scores_windowabst(
                total_scores=tot_auroc_deltas, indices=indices)
        
        return abstaining_func


class EstWindowAbstDeltaAuroc(AbstainerFactory):

    def __init__(self, num_to_abstain_on, return_max_across_windows):
        self.num_to_abstain_on = num_to_abstain_on
        self.return_max_across_windows = return_max_across_windows
    
    def __call__(self, **kwargs):
      
        def abstaining_func(posterior_probs, uncertainties=None,
                            embeddings=None):
            (sorted_probs, indices) = get_sorted_probs_and_indices(
                            posterior_probs=posterior_probs)
            
            window_size = self.num_to_abstain_on
            window_start_idx = np.arange((len(sorted_probs)+1)-window_size)
            window_end_idx = window_start_idx+window_size
            
            est_pos_cumsum = np.array([0]+list(np.cumsum(sorted_probs)))
            est_neg_cumsum = np.array([0]+list(np.cumsum(1-sorted_probs)))
            est_totpos = est_pos_cumsum[-1]
            est_totneg = est_neg_cumsum[-1]
            
            est_sum_negcumsum_at_pos =\
               np.sum(est_neg_cumsum[1:]*sorted_probs)
            est_curr_auroc = est_sum_negcumsum_at_pos/(est_totneg*est_totpos)
            
            #compute the estimated # of positives and negatives in each window
            est_npos_in_window = (est_pos_cumsum[window_size:] -
                                  est_pos_cumsum[:-window_size])
            est_nneg_in_window = window_size-est_npos_in_window
            
            #also estimate the sum of neg_cumsum*labels in each window
            cumsum_negcumsum = np.array(
                [0]+list(np.cumsum(est_neg_cumsum[1:]*sorted_probs)))
            negcumsum_at_pos_in_window = (cumsum_negcumsum[window_size:] -
                                          cumsum_negcumsum[:-window_size])
            #Figure out how sum_negcumsum will be adjusted
            # if a given window is left out. Basically, at each positive
            # above the window, the neg_cumsum will be reduced by the
            # number of negatives within the window.
            est_numpos_above_window = est_totpos - est_pos_cumsum[window_size:]
            adj_est_sum_negcumsum_at_pos = (
                est_sum_negcumsum_at_pos - negcumsum_at_pos_in_window -
             est_numpos_above_window*est_nneg_in_window)
            
            #divide adj_sum_negcumsum by the adjusted totneg and totpos
            # to get the new auroc
            est_new_auroc = adj_est_sum_negcumsum_at_pos/(
                  (est_totpos-est_npos_in_window)
                  *(est_totneg-est_nneg_in_window))
            est_auroc_delta = est_new_auroc - est_curr_auroc
            
            return reorder_scores(
                unreordered_scores=pad_windowed_scores(
                  signal=est_auroc_delta,
                  return_max_across_windows=self.return_max_across_windows,
                  window_size=window_size),
                indices=indices)
        return abstaining_func


class MonteCarloMarginalDeltaRecallAtPrecisionThreshold(
        MonteCarloSampler, AbstainerFactory):

    def __init__(self, n_samples, precision_threshold,
                       smoothing_window_size,
                       polyorder=1, seed=1):
        super(MonteCarloMarginalDeltaRecallAtPrecisionThreshold, self).__init__(
            n_samples=n_samples, smoothing_window_size=smoothing_window_size,
            polyorder=polyorder, seed=seed)
        self.precision_threshold = precision_threshold

    def __call__(self, **kwargs):

        def abstaining_func(posterior_probs,
                            uncertainties=None,
                            embeddings=None):
            (sorted_probs, indices) = get_sorted_probs_and_indices(
                            posterior_probs=posterior_probs)

            tr_vec = np.arange((len(sorted_probs)+1))            
            total_above = len(sorted_probs)-tr_vec
            total_above_rightevict = np.maximum(total_above - 1.0, 1e-7)
            
            tot_recall_deltas = np.zeros(len(sorted_probs))
            
            for sample_num in range(self.n_samples):
                labels, pos_cumsum, neg_cumsum = self.sample(
                    sorted_probs=sorted_probs) 
                totpos = pos_cumsum[-1]
                totneg = neg_cumsum[-1]
                pos_above = totpos - pos_cumsum
                neg_above = totneg - neg_cumsum
                
                if (totpos > 0.0 and totneg > 0.0):
                    #identify the point of target precision,
                    # with and without abstention
                    precision_vec = pos_above/total_above
                    recall_vec = pos_above/totpos
                    precision_posrightevict_vec =(
                       np.maximum(pos_above-1.0, 1e-7)/total_above_rightevict)
                    precision_negrightevict_vec = (
                       pos_above/total_above_rightevict)
                    
                    recall_posrightevict_vec = np.maximum(pos_above-1.0, 0.0)/(
                        max(totpos - 1.0,1e-7))
                    recall_posleftevict_vec = pos_above/(
                        max(totpos - 1.0,1e-7))
                    
                    #take the earliest passing threshold
                    precision_thresh = tr_vec[
                     precision_vec >= self.precision_threshold][0]
                    precision_posrightevict_thresh = tr_vec[
                     precision_posrightevict_vec >= self.precision_threshold][0]                    
                    precision_negrightevict_thresh = tr_vec[
                        precision_negrightevict_vec >= self.precision_threshold
                    ][0]
                    
                    assert precision_negrightevict_thresh <= precision_thresh
                    assert precision_posrightevict_thresh >= precision_thresh
                    
                    #recall when different things are evicted
                    recall_after_eviction = np.zeros(len(sorted_probs))
                    
                    #Abstaining on negatives can cause a shift in threshold.
                    # precision_negrightevict_thresh is the new threshold at
                    # which target precision is attained, assuming a negative to
                    # the left has been abstained on. If a negative to the left
                    # of precision_negrightevict_thresh is abstained on, there
                    # is no shift in threshold and the new recall is the same as
                    # the old recall.
                    recall_after_eviction[
                        (labels==0.0)*(tr_vec[:-1] < precision_negrightevict_thresh)
                    ] = recall_vec[precision_thresh]
                    recall_after_eviction[
                        (labels==0.0)*(tr_vec[:-1] >= precision_negrightevict_thresh)
                    ] = recall_vec[precision_negrightevict_thresh]
                    # Abstaining on positives both changes the recall and,
                    # if the positive is to the right of the new threshold,
                    # affects where the new threshold is.
                    recall_after_eviction[
                        (labels==1.0)*(tr_vec[:-1] >= precision_posrightevict_thresh)
                    ] = recall_posrightevict_vec[precision_posrightevict_thresh]
                    recall_after_eviction[
                        (labels==1.0)*(tr_vec[:-1] < precision_thresh)
                    ] = recall_posleftevict_vec[precision_thresh]
                    #recall for examples evicted in between
                    # precision_thresh and precision_posrightevict_thresh
                    # need to be calculated with care, because when the
                    # threshold can move to the other side of the example
                    # abstained on.
                    tricky_range_recalls = np.zeros(
                        precision_posrightevict_thresh-precision_thresh)
                    i = precision_posrightevict_thresh-1
                    while i >= precision_thresh:
                      if (precision_vec[i+1] >= self.precision_threshold):
                        tricky_range_recalls[
                            i-precision_thresh] = recall_posleftevict_vec[i+1]
                      else:
                        tricky_range_recalls[i-precision_thresh] =\
                         tricky_range_recalls[i+1-precision_thresh]
                      i = i-1
                    recall_after_eviction[
                        (labels==1.0)
                        *(tr_vec[:-1] >= precision_thresh)
                        *(tr_vec[:-1] < precision_posrightevict_thresh)
                    ] = tricky_range_recalls[(labels==1.0)[
                        precision_thresh:precision_posrightevict_thresh]]
                    
                    recall_deltas =\
                      recall_after_eviction - recall_vec[precision_thresh]    
                    tot_recall_deltas += recall_deltas    
                
            return self.postprocess_total_scores_marginalabst(
                total_scores=tot_recall_deltas, indices=indices)

        return abstaining_func


