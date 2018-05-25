# -*- coding: utf-8 -*-
#
#  Copyright 2014-2017 Ramil Nugmanov <stsouko@live.ru>
#  This file is part of CGRtools.
#
#  CGRtools is free software; you can redistribute it and/or modify
#  it under the terms of the GNU Affero General Public License as published by
#  the Free Software Foundation; either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU Affero General Public License for more details.
#
#  You should have received a copy of the GNU Affero General Public License
#  along with this program; if not, write to the Free Software
#  Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
#  MA 02110-1301, USA.
#
from collections import defaultdict
from itertools import cycle
from networkx import connected_components
from operator import sub
from .containers import MoleculeContainer, CGRContainer, ReactionContainer
from .exceptions import InvalidData
from .periodictable import H, O
from .periodictable.factory import _radical_map, _radical_unmap


class CGRcore:
    @staticmethod
    def split(m, meta=False):
        return [m.substructure(c, meta=meta) for c in connected_components(m)]

    @classmethod
    def union(cls, m1, m2):
        """
        union 2 disjoint graphs

        Note: possible data corruption if graphs has mutable attrs. use copy for prevent

        :param m1: Molecule or CGR Container 1
        :param m2: Molecule or CGR Container 2
        :return: united Molecule or CGR Container without metadata from m1 and m2
        """
        if set(m1) & set(m2):
            raise InvalidData('The node sets of m1 and m2 are not disjoint.')

        u = CGRContainer() if isinstance(m1, CGRContainer) or isinstance(m2, CGRContainer) else MoleculeContainer()

        u.add_nodes_from(m1.nodes(data=True))
        u.add_nodes_from(m2.nodes(data=True))
        u.add_edges_from(m1.edges(data=True))
        u.add_edges_from(m2.edges(data=True))

        def fix(m):
            for n, m, attr in m.edges(data=True):
                u.add_edge(n, m, **cls.__fix_attr(attr, u._edge_marks))
            for n, attr in m.nodes(data=True):
                u.add_node(n, p_x=attr['s_x'], p_y=attr['s_y'], p_z=attr['s_z'], **cls.__fix_attr(attr, u._node_marks))

        if isinstance(u, CGRContainer) and not isinstance(m1, CGRContainer):
            fix(m1)
        elif isinstance(u, CGRContainer) and not isinstance(m2, CGRContainer):
            fix(m2)
        return u

    @classmethod
    def compose(cls, m1, m2, balance=False):
        """
        compose 2 graphs to CGR

        :param m1: Molecule or CGR Container 1
        :param m2: Molecule or CGR Container 2
        :return: CGRContainer
        """
        common = set(m1).intersection(m2)
        h = CGRContainer()
        unbalanced = {}

        for i, g in (('reagents', m1), ('products', m2)):
            is_cgr = isinstance(g, CGRContainer)
            pdi = cls.__popdict[i][is_cgr]
            unbalanced[i] = ext_common = defaultdict(list)
            e_pop, n_pop = pdi['edge'], pdi['node']

            for n, m, attr in g.edges(common, data=True):
                if n in common:
                    if m not in common:
                        ext_common[m].append(n)
                elif m in common:
                    ext_common[n].append(m)

                bond = {e_pop[k]: v for k, v in attr.items() if k in e_pop}
                if bond:
                    h.add_edge(n, m, **bond)

            unique = set(g).difference(common).difference(ext_common)

            for n, m, attr in g.edges(unique, data=True):
                if is_cgr:
                    tmp = attr
                else:
                    tmp = dict(p_bond=attr['s_bond'], **attr)
                    s = attr.get('s_stereo')
                    if s:
                        tmp['p_stereo'] = s
                h.add_edge(n, m, **tmp)

            for n in common:
                h.add_node(n, **{n_pop[k]: v for k, v in g.nodes[n].items() if k in n_pop})

            for n in unique:
                attr = g.nodes[n]
                h.add_node(n, **(attr if is_cgr else
                                 dict(p_charge=attr['s_charge'], p_x=attr['s_x'], p_y=attr['s_y'], p_z=attr['s_z'],
                                      p_neighbors=attr.get('s_neighbors'), p_hyb=attr.get('s_hyb'),
                                      p_radical=attr.get('s_radical'), p_stereo=attr.get('s_stereo'), **attr)))

            for n in ext_common:
                """ skip neighbors, hyb, stereo, charge, radical data from skin atoms
                """
                attr = g.nodes[n]
                if is_cgr:
                    tmp = {k: v for k, v in attr.items() if k not in pdi['ext_node']}
                elif i == 'reagents':
                    tmp = dict(p_x=attr['s_x'], p_y=attr['s_y'], p_z=attr['s_z'],
                               **{k: v for k, v in attr.items() if k in n_pop})
                else:
                    tmp = dict(s_x=attr['s_x'], s_y=attr['s_y'], s_z=attr['s_z'],
                               mark=attr['mark'], element=attr['element'], map=attr['map'],
                               **{n_pop[k]: v for k, v in attr.items() if k in n_pop})
                h.add_node(n, **tmp)

        if not balance:
            for n in unbalanced['reagents']:
                attr = h.nodes[n]
                attr['p_charge'] = attr['s_charge']
                attr['p_radical'] = attr.get('s_radical')

            for n in unbalanced['products']:
                attr = h.nodes[n]
                attr['s_charge'] = attr['p_charge']
                attr['s_radical'] = attr.get('p_radical')

        else:
            """ calc unbalanced charges and radicals for skin atoms
            """
            reverse_ext = defaultdict(lambda: dict(reagents=[], products=[]))
            for i, e in unbalanced.items():
                for n, c in e.items():
                    for j in c:
                        reverse_ext[j][i].append(n)

            for n, sp in reverse_ext.items():
                s_atom, p_atom = h.atom(n)
                sr, pr = s_atom.radical, p_atom.radical
                dr = pr - sr
                # radical balancing
                if dr > 0:  # radical added or increased.
                    if sp['reagents']:
                        for m in sp['reagents']:
                            attr = h.nodes[m]
                            attr['p_charge'] = attr['s_charge']
                        for _, m in zip(range(dr), cycle(sp['reagents'])):  # homolysis
                            attr = h.nodes[m]
                            attr['p_radical'] = \
                                _radical_unmap[_radical_map[attr.get('p_radical', attr.get('s_radical'))] + 1]
                            h.meta.setdefault('rule #14. atom lost. common atom radical added or increased. '
                                              'lost atom radical added', []).append((m, n))
                        for m in sp['reagents'][dr:]:
                            attr = h.nodes[m]
                            attr.update(p_radical=attr.get('s_radical'))
                            h.meta.setdefault('rule #15. atom lost. common atom radical added or increased. '
                                              'lost atom radical unchanged', []).append((m, n))
                        for m in sp['products']:
                            attr = h.nodes[m]
                            attr.update(s_charge=attr['p_charge'], s_radical=attr.get('p_radical'))
                            h.meta.setdefault('rule #16. atom new. common atom radical added or increased. '
                                              'new atom radical unchanged', []).append((m, n))
                    else:
                        for m in sp['products']:
                            attr = h.nodes[m]
                            attr['s_charge'] = attr['p_charge']
                        for _, m in zip(range(dr), cycle(sp['products'])):  # radical addition
                            attr = h.nodes[m]
                            attr['s_radical'] = \
                                _radical_unmap[_radical_map[attr.get('s_radical', attr.get('p_radical'))] + 1]
                            h.meta.setdefault('rule #17. atom new. common atom radical added or increased. '
                                              'new atom radical added', []).append((m, n))
                        for m in sp['products'][dr:]:
                            attr = h.nodes[m]
                            attr.update(s_radical=attr.get('p_radical'))
                            h.meta.setdefault('rule #16. atom new. common atom radical added or increased. '
                                              'new atom radical unchanged', []).append((m, n))

                elif dr < 0:  # radical removed or decreased.
                    if sp['products']:
                        for m in sp['products']:
                            attr = h.nodes[m]
                            attr['s_charge'] = attr['p_charge']
                        for _, m in zip(range(-dr), cycle(sp['products'])):  # recombination
                            attr = h.nodes[m]
                            attr['s_radical'] = \
                                _radical_unmap[_radical_map[attr.get('s_radical', attr.get('p_radical'))] + 1]
                            h.meta.setdefault('rule #18. atom new. common atom radical removed or decreased. '
                                              'new atom radical added', []).append((m, n))
                        for m in sp['products'][-dr:]:
                            attr = h.nodes[m]
                            attr.update(s_radical=attr.get('p_radical'))
                            h.meta.setdefault('rule #19. atom new. common atom radical removed or decreased. '
                                              'new atom radical unchanged', []).append((m, n))
                        for m in sp['reagents']:
                            attr = h.nodes[m]
                            attr.update(p_charge=attr['s_charge'], p_radical=attr.get('s_radical'))
                            h.meta.setdefault('rule #20. atom lost. common atom radical removed or decreased. '
                                              'lost atom radical unchanged', []).append((m, n))
                    else:
                        for m in sp['reagents']:
                            attr = h.nodes[m]
                            attr['p_charge'] = attr['s_charge']
                        for _, m in zip(range(-dr), cycle(sp['reagents'])):  # radical elimination
                            attr = h.nodes[m]
                            attr['p_radical'] = \
                                _radical_unmap[_radical_map[attr.get('p_radical', attr.get('s_radical'))] + 1]
                            h.meta.setdefault('rule #21. atom lost. common atom radical removed or decreased. '
                                              'lost atom radical added', []).append((m, n))
                        for m in sp['reagents'][-dr:]:
                            attr = h.nodes[m]
                            attr.update(s_radical=attr.get('p_radical'))
                            h.meta.setdefault('rule #20. atom lost. common atom radical removed or decreased. '
                                              'lost atom radical unchanged', []).append((m, n))
                else:
                    sv = s_atom.check_valence(*h._get_atom_environment(n))
                    pv = p_atom.check_valence(*h._get_atom_environment(n, 'p'))
                    sh, ph = h.atom_total_h(n)
                    sc, pc = s_atom.charge, p_atom.charge

                    dv = pv - sv
                    dh = ph - sh
                    dc = pc - sc

                    if dv == dh == dc:  # explicit hydrogen addition/removing
                        if dv > 0:
                            for m in sp['products']:
                                h.nodes[m]['s_charge'] = 1
                                h.meta.setdefault('rule #3. atom new. common atom protonation', []).append((m, n))
                        else:
                            for m in sp['reagents']:
                                h.nodes[m]['p_charge'] = 1
                                h.meta.setdefault('rule #4. atom lost. common atom deprotonation', []).append((m, n))

                    elif not (dv or dh or dc):  # common atom changed. Substitution, Elimination, Addition
                        for m in sp['reagents']:
                            attr = h.nodes[m]
                            attr.update(p_charge=attr['s_charge'], p_radical=attr.get('s_radical'))
                            h.meta.setdefault('rule #1. atom lost. common atom unchanged. '
                                              'substitution, elimination, addition', []).append((m, n))
                        for m in sp['products']:
                            attr = h.nodes[m]
                            attr.update(s_charge=attr['p_charge'], s_radical=attr.get('p_radical'))
                            h.meta.setdefault('rule #2. atom new. common atom unchanged. '
                                              'substitution, elimination, addition', []).append((m, n))
                    else:
                        for m in sp['reagents']:
                            attr = h.nodes[m]
                            attr.update(p_charge=attr['s_charge'], p_radical=attr.get('s_radical'))
                            h.meta.setdefault('rule #5. atom lost. common atom changed. '
                                              'convert to reduction or oxidation', []).append((m, n))
                        for m in sp['products']:
                            attr = h.nodes[m]
                            attr.update(s_charge=attr['p_charge'], s_radical=attr.get('p_radical'))
                            h.meta.setdefault('rule #6. atom new. common atom changed. '
                                              'convert to reduction or oxidation', []).append((m, n))

                        pth = ph + sum(h.atom_total_h(x)[1] for x in sp['reagents'])
                        sth = sh + sum(h.atom_total_h(x)[0] for x in sp['products'])
                        dth = pth - sth

                        # charge neutralization
                        if dc > 0:
                            for _ in range(dc):
                                h.meta.setdefault('rule #7. charge neutralization. hydroxide radical added',
                                                  []).append(h.add_atom(O(multiplicity=2), O(charge=-1)))
                        elif dc < 0:
                            for _ in range(-dc):
                                h.meta.setdefault('rule #8. charge neutralization. hydrogen radical added',
                                                  []).append(h.add_atom(H(multiplicity=2), H(charge=1)))

                        # hydrogen balancing
                        if dth > 0:
                            red_e = 0
                            for m in sp['products']:
                                if h.nodes[m]['element'] == 'H':  # set reduction H if explicit H count increased
                                    h.nodes[m]['s_radical'] = 2
                                    red_e += 1
                                    h.meta.setdefault('rule #11. protonation. new explicit hydrogen radical added',
                                                      []).append(m)

                            red = []
                            for _ in range(dth - red_e):  # add reduction agents
                                m = h.add_atom(H(multiplicity=2), H())
                                red.append(m)
                                h.meta.setdefault('rule #10. protonation. hydrogen radical added', []).append(m)
                            red = iter(red)

                            dih = sub(*h.atom_implicit_h(n))
                            if dih < 0:  # attach reduction H to central atom if implicit H atoms count increased
                                for _ in range(-dih):
                                    m = next(red)
                                    h.add_bond(m, n, None)
                                    h.meta.setdefault('rule #12. protonation. new implicit hydrogen radical added',
                                                      []).append(m)

                            for m in sp['reagents']:  # attach reduction H if detached group implicit H count increased
                                dih = sub(*h.atom_implicit_h(m))
                                if dih < 0:
                                    for _ in range(-dih):
                                        o = next(red)
                                        h.add_bond(o, m, None)
                        elif dth < 0:
                            oxo = []
                            for _ in range(-dth):
                                m = h.add_atom(O(multiplicity=2), O())
                                oxo.append(m)
                                h.meta.setdefault('rule #9. deprotonation. hydroxide radical added', []).append(m)
                            oxo = iter(oxo)

                            for m in sp['reagents']:
                                if h.nodes[m]['element'] == 'H':
                                    o = next(oxo)
                                    h.add_bond(o, m, None)
                                    h.meta.setdefault('rule #13. hydrogen accepting by hydroxide radical added',
                                                      []).append(m)

        """ update sp_* marks
        """
        h.fix_data()
        return h

    @classmethod
    def decompose(cls, g):
        tmp = ReactionContainer(meta=g.meta)

        x = MoleculeContainer(g)
        for n, m in cls.__get_broken_paths(g, 's_bond'):
            x.remove_edge(n, m)

        for mol in cls.split(x):
            mol.fix_data()
            tmp.reagents.append(mol)

        x = MoleculeContainer(g)
        for n, m in cls.__get_broken_paths(g, 'p_bond'):
            x.remove_edge(n, m)

        for mol in cls.split(x):
            for *_, edge_attr in mol.edges(data=True):
                edge_attr['s_bond'] = edge_attr.get('p_bond')
                s = edge_attr.get('p_stereo')
                if s:
                    edge_attr['s_stereo'] = s
                else:
                    edge_attr.pop('s_stereo', None)
            for _, node_attr in mol.nodes(data=True):
                for i, j in (('p_x', 's_x'), ('p_y', 's_y'), ('p_z', 's_z'), ('p_neighbors', 's_neighbors'),
                             ('p_hyb', 's_hyb'), ('p_charge', 's_charge'), ('p_radical', 's_radical')):
                    mark = node_attr.get(i)
                    if mark is not None:
                        node_attr[j] = mark
                    else:
                        node_attr.pop(j, None)

            mol.fix_data()
            tmp.products.append(mol)

        return tmp

    @staticmethod
    def __fix_attr(attr, marks):
        tmp = {}
        for s, p, sp in marks:
            if attr.get(s):
                tmp[p] = tmp[sp] = attr[s]
        return tmp

    @staticmethod
    def __get_broken_paths(g, edge):
        for m, n, attr in g.edges(data=True):
            if attr.get(edge) is None:
                yield n, m

    __tmp = dict(edge=dict(s_bond='s_bond', s_stereo='s_stereo'),
                 ext_node=('p_neighbors', 'p_hyb', 'p_stereo', 'p_charge', 'p_radical',
                           'sp_neighbors', 'sp_hyb', 'sp_stereo', 'sp_charge', 'sp_radical'),
                 node=dict(s_charge='s_charge', s_neighbors='s_neighbors', s_hyb='s_hyb',
                           s_x='s_x', s_y='s_y', s_z='s_z', s_stereo='s_stereo', s_radical='s_radical',
                           mark='mark', element='element', map='map'))

    __popdict = dict(reagents={True: __tmp, False: __tmp},
                     products={True: dict(edge=dict(p_bond='p_bond', p_stereo='p_stereo'),
                                          ext_node=('s_neighbors', 's_hyb', 's_stereo', 's_charge', 's_radical',
                                                    'sp_neighbors', 'sp_hyb', 'sp_stereo', 'sp_charge', 'sp_radical'),
                                          node=dict(p_charge='p_charge', p_neighbors='p_neighbors', p_hyb='p_hyb',
                                                    p_x='p_x', p_y='p_y', p_z='p_z', p_stereo='p_stereo',
                                                    p_radical='p_radical')),
                               False: dict(edge=dict(s_bond='p_bond', s_stereo='p_stereo'),
                                           node=dict(s_charge='p_charge', s_neighbors='p_neighbors', s_hyb='p_hyb',
                                                     s_x='p_x', s_y='p_y', s_z='p_z', s_stereo='p_stereo',
                                                     s_radical='p_radical'))})
