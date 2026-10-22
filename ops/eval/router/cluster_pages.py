# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "scikit-learn"]
# ///
"""Groups the brain's pages into themes by their embeddings (mean of the chunks).

Input: TSV (slug, type, date, title, pgvector embedding as text) exported from the lab database:
  psql -d gbrain_lab_router -At -F $'\\t' -c "select p.slug, p.type, to_char(coalesce(p.effective_date,p.created_at),'YYYY-MM-DD'),
    p.title, avg(c.embedding)::text from pages p join content_chunks c on c.page_id=p.id
    where p.deleted_at is null and c.embedding is not null group by p.id" > page-emb.tsv
Method: spherical k-means (normalised vectors), k picked by silhouette among --ks;
a group of more than --max-size pages is split in two; a group of fewer than
--min-size pages is merged into the nearest centre.
Output: JSON [{id, size, slugs, titles, types, dates}].

Usage: uv run ops/eval/router/cluster_pages.py page-emb.tsv --out clusters.json [--ks 20,25,30,35,40]
"""
import argparse
import json

import numpy as np
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score


def load(path):
    rows, vecs = [], []
    for line in open(path, encoding='utf-8'):
        slug, typ, date, title, emb = line.rstrip('\n').split('\t')
        if slug.startswith('themes/'):
            continue
        rows.append({'slug': slug, 'type': typ, 'date': date, 'title': title})
        vecs.append(np.array(json.loads(emb), dtype=np.float32))
    x = np.vstack(vecs)
    return rows, x / np.linalg.norm(x, axis=1, keepdims=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('tsv')
    ap.add_argument('--out', required=True)
    ap.add_argument('--ks', default='20,25,30,35,40')
    ap.add_argument('--max-size', type=int, default=60)
    ap.add_argument('--min-size', type=int, default=5)
    ap.add_argument('--seed', type=int, default=21)
    args = ap.parse_args()
    rows, x = load(args.tsv)

    best = None
    for k in [int(v) for v in args.ks.split(',')]:
        km = KMeans(n_clusters=k, n_init=10, random_state=args.seed).fit(x)
        s = silhouette_score(x, km.labels_, metric='cosine')
        print(f'k={k} silhouette={s:.4f}')
        if best is None or s > best[0]:
            best = (s, k, km)
    _, k, km = best
    labels = km.labels_.copy()
    print(f'k retenu : {k}')

    # Split the groups that are too big.
    nxt = labels.max() + 1
    for c in list(np.unique(labels)):
        idx = np.where(labels == c)[0]
        if len(idx) > args.max_size:
            sub = KMeans(n_clusters=2, n_init=10, random_state=args.seed).fit(x[idx]).labels_
            labels[idx[sub == 1]] = nxt
            nxt += 1
    # Merge the groups that are too small into the nearest centre.
    while True:
        ids, counts = np.unique(labels, return_counts=True)
        small = [c for c, n in zip(ids, counts) if n < args.min_size]
        if not small:
            break
        c = small[0]
        others = [o for o in ids if o != c]
        cents = np.vstack([x[labels == o].mean(0) for o in others])
        for i in np.where(labels == c)[0]:
            labels[i] = others[int(np.argmax(cents @ x[i]))]

    out = []
    for n, c in enumerate(sorted(np.unique(labels), key=lambda c: -(labels == c).sum())):
        idx = np.where(labels == c)[0]
        cent = x[idx].mean(0)
        idx = idx[np.argsort(-(x[idx] @ cent))]  # from the most central to the most peripheral
        out.append({'id': f'c{n:02d}', 'size': len(idx), 'slugs': [rows[i]['slug'] for i in idx],
                    'titles': [rows[i]['title'] for i in idx], 'types': [rows[i]['type'] for i in idx],
                    'dates': [rows[i]['date'] for i in idx]})
    json.dump(out, open(args.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    for c in out:
        print(f"{c['id']} {c['size']:3}  {' | '.join(t[:40] for t in c['titles'][:3])}")


if __name__ == '__main__':
    main()
