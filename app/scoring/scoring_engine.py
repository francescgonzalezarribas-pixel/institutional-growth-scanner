def calculate_score(data):

    score = 0

    if data["relative_volume"] >= 2:
        score += 20

    if data["relative_volume"] >= 4:
        score += 10

    if data["has_news"]:
        score += 25

    if data["breakout"]:
        score += 20

    if data["ipo"]:
        score += 10

    if data["sector_hot"]:
        score += 10

    if data["institutional_volume"]:
        score += 15

    return min(score, 100)