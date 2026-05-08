def calculate_score(data):

    score = 0

    rv = data["relative_volume"]

    if rv >= 2:
        score += 15

    if rv >= 3:
        score += 10

    if rv >= 5:
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
        score += 10

    return min(score, 100)