from django.db.models import Q

from projects.models import Video
from recording.models import Comparison

K_FACTOR = 32


def update_elo(video_left, video_right, result):
    """
    Update Elo ratings for both videos based on the comparison result.

    Uses the standard Elo formula:
        E_a = 1 / (1 + 10^((R_b - R_a) / 400))
        R_a' = R_a + K * (S_a - E_a)

    Args:
        video_left: Video instance (left side of comparison).
        video_right: Video instance (right side of comparison).
        result: One of 'left', 'right', or 'equal'.
    """
    r_left = video_left.elo_rating
    r_right = video_right.elo_rating

    expected_left = 1.0 / (1.0 + 10.0 ** ((r_right - r_left) / 400.0))
    expected_right = 1.0 - expected_left

    if result == 'left':
        score_left = 1.0
        score_right = 0.0
    elif result == 'right':
        score_left = 0.0
        score_right = 1.0
    else:  # equal
        score_left = 0.5
        score_right = 0.5

    video_left.elo_rating = r_left + K_FACTOR * (score_left - expected_left)
    video_right.elo_rating = r_right + K_FACTOR * (score_right - expected_right)

    video_left.comparison_count += 1
    video_right.comparison_count += 1

    Video.objects.bulk_update(
        [video_left, video_right],
        ['elo_rating', 'comparison_count'],
    )


def select_next_pair(project_id):
    """
    Select the next pair of videos to compare for a project.

    Strategy:
        1. Prioritise least-compared videos.
        2. Among those, prefer pairs that have never been compared.

    Returns:
        A tuple (video_a, video_b) or None if fewer than 2 videos exist.
    """
    videos = list(
        Video.objects.filter(project_id=project_id).order_by('comparison_count', '?')
    )

    if len(videos) < 2:
        return None

    # Build a set of already-compared pairs for quick lookup.
    compared_pairs = set()
    comparisons = Comparison.objects.filter(project_id=project_id).values_list(
        'video_left_id', 'video_right_id',
    )
    for left_id, right_id in comparisons:
        compared_pairs.add((left_id, right_id))
        compared_pairs.add((right_id, left_id))

    # Try to find an uncompared pair among the least-compared videos.
    for i, video_a in enumerate(videos):
        for video_b in videos[i + 1:]:
            if (video_a.id, video_b.id) not in compared_pairs:
                return (video_a, video_b)

    # All pairs have been compared at least once; return the two
    # least-compared videos so the user can keep refining rankings.
    return (videos[0], videos[1])


def get_ranking_progress(project_id):
    """
    Return ranking progress for a project.

    Returns:
        dict with keys: completed, total, percent.
        total is n*(n-1)/2 (every unique pair once).
    """
    n = Video.objects.filter(project_id=project_id).count()
    total = n * (n - 1) // 2
    completed = Comparison.objects.filter(project_id=project_id).count()
    percent = round((completed / total) * 100, 1) if total > 0 else 0.0

    return {
        'completed': completed,
        'total': total,
        'percent': percent,
    }
