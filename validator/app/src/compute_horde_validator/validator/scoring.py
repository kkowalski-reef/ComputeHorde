import logging
from collections import defaultdict
from functools import partial

import numpy as np
from compute_horde.executor_class import ExecutorClass
from django.conf import settings

logger = logging.getLogger(__name__)


EXECUTOR_CLASS_WEIGHTS = {
    ExecutorClass.spin_up_4min__gpu_24gb: 100,
}


def normalize(scores, weight=1):
    total = sum(scores.values())
    if total == 0:
        return scores
    return {hotkey: weight * score / total for hotkey, score in scores.items()}


def sigmoid(x, beta, delta):
    return 1 / (1 + np.exp(beta * (-x + delta)))


def reversed_sigmoid(x, beta, delta):
    return sigmoid(-x, beta=beta, delta=-delta)


def horde_score(benchmarks, alpha=0, beta=0, delta=0):
    """Proportionally scores horde benchmarks allowing increasing significance for chosen features

    By default scores are proportional to horde "strength" - having 10 executors would have the same
    score as separate 10 single executor miners. Subnet owner can control significance of defined features:

    alpha - controls significance of average score, so smaller horde can have higher score if executors are stronger;
            best values are from range [0, 1], with 0 meaning no effect
    beta - controls sigmoid function steepness; sigmoid function is over `-(1 / horde_size)`, so larger hordes can be
           more significant than smaller ones, even if summary strength of a horde is the same;
           best values are from range [0,5] (or more, but higher values does not change sigmoid steepnes much),
           with 0 meaning no effect
    delta - controls where sigmoid function has 0.5 value allowing for better control over effect of beta param;
            best values are from range [0, 1]
    """
    sum_agent = sum(benchmarks)
    inverted_n = 1 / len(benchmarks)
    avg_benchmark = sum_agent * inverted_n
    scaled_inverted_n = reversed_sigmoid(inverted_n, beta=10**beta, delta=delta)
    scaled_avg_benchmark = avg_benchmark**alpha
    return scaled_avg_benchmark * sum_agent * scaled_inverted_n


def score_jobs(jobs, score_aggregation=sum, normalization_weight=1):
    batch_scores = defaultdict(list)
    score_per_hotkey = {}
    for job in jobs:
        hotkey = job.miner.hotkey
        batch_scores[hotkey].append(job.score)
    for hotkey, hotkey_batch_scores in batch_scores.items():
        score_per_hotkey[hotkey] = score_aggregation(hotkey_batch_scores)
    return normalize(score_per_hotkey, weight=normalization_weight)


def score_batch(batch):
    executor_class_jobs = defaultdict(list)
    for job in batch.synthetic_jobs.all():
        if job.executor_class in EXECUTOR_CLASS_WEIGHTS:
            executor_class_jobs[job.executor_class].append(job)

    parametriezed_horde_score = partial(
        horde_score,
        # scaling factor for avg_score of a horde - best in range [0, 1] (0 means no effect on score)
        alpha=settings.HORDE_SCORE_AVG_PARAM,
        # sigmoid steepnes param - best in range [0, 5] (0 means no effect on score)
        beta=settings.HORDE_SCORE_SIZE_PARAM,
        # horde size for 0.5 value of sigmoid - sigmoid is for 1 / horde_size
        delta=1 / settings.HORDE_SCORE_CENTRAL_SIZE_PARAM,
    )
    batch_scores = defaultdict(float)
    for executor_class, jobs in executor_class_jobs.items():
        executor_class_weight = EXECUTOR_CLASS_WEIGHTS[executor_class]
        if executor_class == ExecutorClass.spin_up_4min__gpu_24gb:
            score_aggregation = parametriezed_horde_score
        else:
            score_aggregation = sum

        executors_class_scores = score_jobs(
            jobs,
            score_aggregation=score_aggregation,
            normalization_weight=executor_class_weight,
        )
        for hotkey, score in executors_class_scores.items():
            batch_scores[hotkey] += score
    return batch_scores


def score_batches(batches):
    hotkeys_scores = defaultdict(float)
    for batch in batches:
        batch_scores = score_batch(batch)
        for hotkey, score in batch_scores.items():
            hotkeys_scores[hotkey] += score
    return hotkeys_scores
