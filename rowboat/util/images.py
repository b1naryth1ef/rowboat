from collections import namedtuple
from math import sqrt
import random


Point = namedtuple('Point', ('coords', 'n', 'ct'))
Cluster = namedtuple('Cluster', ('points', 'center', 'n'))


def get_points(img):
    points = []
    w, h = img.size
    for count, color in img.getcolors(w * h):
        points.append(Point(color, 3, count))
    return points


def rtoh(rgb):
    return '%s' % ''.join(('%02x' % p for p in rgb))


def get_dominant_colors(img, n=3):
    img.thumbnail((1024, 1024))
    w, h = img.size

    points = get_points(img)
    clusters = kmeans(points, n, 1)
    rgbs = [map(int, c.center.coords) for c in clusters]
    return map(rtoh, rgbs)


def get_dominant_colors_user(user, url=None):
    import requests
    from rowboat.redis import rdb
    from PIL import Image
    from six import BytesIO

    key = 'avatar:color:{}'.format(user.avatar)
    if rdb.exists(key):
        return int(rdb.get(key))
    else:
        r = requests.get(url or user.avatar_url)
        try:
            r.raise_for_status()
        except:
            return 0
        color = int(get_dominant_colors(Image.open(BytesIO(r.content)))[0], 16)
        rdb.set(key, color)
        return color


def get_dominant_colors_guild(guild):
    import requests
    from rowboat.redis import rdb
    from PIL import Image
    from six import BytesIO

    key = 'guild:color:{}'.format(guild.icon)
    if rdb.exists(key):
        return int(rdb.get(key))
    else:
        r = requests.get(guild.icon_url)
        try:
            r.raise_for_status()
        except:
            return 0
        color = int(get_dominant_colors(Image.open(BytesIO(r.content)))[0], 16)
        rdb.set(key, color)
        return color


def euclidean(p1, p2):
    return sqrt(sum([
        (p1.coords[i] - p2.coords[i]) ** 2 for i in range(p1.n)
    ]))


def calculate_center(points, n):
    vals = [0.0 for i in range(n)]
    plen = 0
    for p in points:
        plen += p.ct
        for i in range(n):
            vals[i] += (p.coords[i] * p.ct)
    return Point([(v / plen) for v in vals], n, 1)


def kmeans(points, k, min_diff):
    clusters = [Cluster([p], p, p.n) for p in random.sample(points, k)]

    while 1:
        plists = [[] for i in range(k)]

        for p in points:
            smallest_distance = float('Inf')
            for i in range(k):
                distance = euclidean(p, clusters[i].center)
                if distance < smallest_distance:
                    smallest_distance = distance
                    idx = i
            plists[idx].append(p)

        diff = 0
        for i in range(k):
            old = clusters[i]
            center = calculate_center(plists[i], old.n)
            new = Cluster(plists[i], center, old.n)
            clusters[i] = new
            diff = max(diff, euclidean(old.center, new.center))

        if diff < min_diff:
            break

    return clusters
