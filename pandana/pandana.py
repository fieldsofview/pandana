import time
import matplotlib
import brewer2mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from . import _pyaccess

MAX_NUM_NETWORKS = 0
NUM_NETWORKS = 0

AGGREGATIONS = {
    "SUM": 0,
    "AVE": 1,
    "STD": 5,
    "COUNT": 6
}

DECAYS = {
    "EXP": 0,
    "LINEAR": 1,
    "FLAT": 2
}


def reserve_num_graphs(num):
    global NUM_NETWORKS, MAX_NUM_NETWORKS
    assert MAX_NUM_NETWORKS == 0, ("Global memory used so cannot initialize "
                                   "twice")
    assert num > 0
    MAX_NUM_NETWORKS = num
    _pyaccess.create_graphs(num)


def from_networkx(G):
    nids = []
    lats = []
    lons = []
    for n in G.nodes_iter():
        n = G.node[n]['data']
        nids.append(int(n.id))
        lats.append(n.lat)
        lons.append(n.lon)
    nodes = pd.DataFrame({'x': lons, 'y': lats}, index=nids)

    froms = []
    tos = []
    weights = []
    for e in G.edges_iter():
        e = G.get_edge_data(*e)['data']
        froms.append(int(G.node[e.nds[0]]['data'].id))
        tos.append(int(G.node[e.nds[1]]['data'].id))
        weights.append(float(1))
    edges = pd.DataFrame({'from': froms, 'to': tos, 'weight': weights})

    return nodes, edges


class Network:

    def _node_indexes(self, node_ids):
        # for some reason, merge is must faster than .loc
        df = pd.merge(pd.DataFrame({"node_ids": node_ids}),
                      pd.DataFrame({"node_idx": self.node_idx}),
                      left_on="node_ids",
                      right_index=True,
                      how="left")
        return df.node_idx

    @property
    def node_ids(self):
        return self.node_idx.index

    def __init__(self, node_x, node_y, edge_from, edge_to, edge_weights,
                 twoway=False):
        """
        Create the transportation network in the city.  Typical data would be
        distance based from OpenStreetMap or possibly using transit data from
        GTFS.

        Parameters
        ----------
        node_x: Pandas Series, flaot
            Defines the x attribute for nodes in the network (e.g. longitude)
        node_y: Pandas Series, float
            Defines the y attribute for nodes in the network (e.g. latitude)
            This param and the one above should have the *same* index which
            should be the node_ids that are referred to in the edges below.
        edge_from: Pandas Series, int
            Defines the node id that begins an edge - should refer to the index
            of the two series objects above
        edge_to: Pandas Series, int
            Defines the node id that ends an edge - should refer to the index
            of the two series objects above
        edge_weights: Pandas DataFrame, all floats
            Specifies one or more *impedances* on the network which define the
            distances between nodes.  Multiple impedances can be used to
            capture travel times at different times of day, for instance
        two_way : boolean, optional
            Whether the edges in this network are two way edges or one way (
            where the one direction is directed from the from node to the to
            node)

        Returns
        -------
        Network object
        """
        global NUM_NETWORKS, MAX_NUM_NETWORKS

        if MAX_NUM_NETWORKS == 0:
            reserve_num_graphs(1)

        assert NUM_NETWORKS < MAX_NUM_NETWORKS, "Adding more networks than " \
                                                "have been reserved"
        self.graph_no = NUM_NETWORKS
        NUM_NETWORKS += 1

        nodes_df = pd.DataFrame({'x': node_x, 'y': node_y})
        edges_df = pd.DataFrame({'from': edge_from, 'to': edge_to}).\
            join(edge_weights)
        self.nodes_df = nodes_df
        self.edges_df = edges_df

        self.impedance_names = list(edge_weights.columns)
        self.variable_names = []

        # this maps ids to indexes which are used internally
        self.node_idx = pd.Series(np.arange(len(nodes_df)),
                                  index=nodes_df.index)

        edges = pd.concat([self._node_indexes(edges_df["from"]),
                          self._node_indexes(edges_df["to"])], axis=1)

        _pyaccess.create_graph(self.graph_no,
                               nodes_df.index.astype('int32'),
                               nodes_df.astype('float32'),
                               edges.astype('int32'),
                               edges_df[edge_weights.columns].transpose()
                                   .astype('float32'),
                               twoway)

    def set(self, node_ids, variable=None, name="tmp"):
        """
        Characterize urban space with a variable that is related to nodes in
        the network.

        Parameters
        ----------
        node_id : Pandas Series, int
            A series of node_ids which are usually computed using
            get_node_ids on this object.
        variable : Pandas Series, float, optional
            A series which represents some variable defined in urban space.
            It could be the location of buildings, or the income of all
            households - just about anything can be aggregated using the
            network queries provided here and this provides the api to set
            the variable at its disaggregate locations.  Note that node_id
            and variable should have the same index (although the index is
            not actually used).  If variable is not set, then it is assumed
            that the variable is all "ones" at the location specified by
            node_ids.  This could be, for instance, the location of all
            coffee shops which don't really have a variable to aggregate.
        name : string, optional
            Name the variable.  This is optional in the sense that if you don't
            specify it, the default name will be used.  Since the same
            default name is used by aggregate on this object, you can
            alternate between characterize and aggregate calls without
            setting names.

        Returns
        -------
        Nothing
        """

        if variable is None:
            variable = pd.Series(np.ones(len(node_ids)), index=node_ids.index)

        df = pd.DataFrame({name: variable,
                           "node_idx": self._node_indexes(node_ids)})

        t1 = time.time()
        l = len(df)
        df = df.dropna(how="any")
        newl = len(df)
        if newl-l > 0:
            print "Removed %d rows because they contain missing values" % \
                (newl-l)
        print "up %.3f" % (time.time()-t1)

        if name not in self.variable_names:
            self.variable_names.append(name)
            _pyaccess.initialize_acc_vars(self.graph_no,
                                          len(self.variable_names))

        print df.describe()

        t1 = time.time()
        _pyaccess.initialize_acc_var(self.graph_no,
                                     self.variable_names.index(name),
                                     df.node_idx.astype('int32'),
                                     df[name].astype('float32'))
        print "%.3f" % (time.time()-t1)

    def precompute(self, distance):
        """
        Precomputes the range queries (the reachable nodes within this
        maximum distance so as long as you use a smaller distance, cached
        results will be used.

        Parameters
        ----------
        distance : float
            The maximum distance to use

        Returns
        -------
        Nothing
        """
        _pyaccess.precompute_range(distance, self.graph_no)

    def aggregate(self, distance, type="sum", decay="linear", imp_name=None,
                  name="tmp"):
        """
        Aggregate information for every source node in the network - this is
        really the main purpose of this library.  This allows you to touch
        the data specified by calling set and perform some aggregation on it
        within the specified distance.  For instance, summing the population
        within 1000 meters.

        Parameters
        ----------
        distance : float
            The maximum distance to aggregate data within
        type : string
            The type of aggregation, can be one of "ave", "sum", "std",
            and "count"
        decay : string
            The type of decay to apply, which makes things that are further
            away count less in the aggregation - must be one of "linear",
            "exponential" or "flat" (which means no decay).  Linear is the
            fastest computation to perform.  When performing an "ave",
            the decay is typically "flat"
        imp_name : string, optional
            The impedance name to use for the aggregation on this network.
            Must be one of the impedance names passed in the constructor of
            this object.  If not specified, there must be only one impedance
            passed in the constructor, which will be used.
        name : string, optional
            The variable to aggregate.  This variable will have been created
            and named by a call to set.  If not specified, the default
            variable name will be used so that the most recent call to set
            without giving a name will be the variable used.

        Returns
        -------
        agg : Pandas Series
            Returns a Pandas Series for every origin node in the network,
            with the index which is the same as the node_ids passed to the
            init method and the values are the aggregations for each source
            node in the network.
        """
        agg = AGGREGATIONS[type.upper()]
        decay = DECAYS[decay.upper()]

        if imp_name is None:
            assert len(self.impedance_names) == 1,\
                "must pass impedance name if there are multiple impedances set"
            imp_name = self.impedance_names[0]

        assert imp_name in self.impedance_names, "An impedance with that name" \
                                                 "was not found"
        imp_num = self.impedance_names.index(imp_name)

        gno = self.graph_no

        assert name in self.variable_names, "A variable with that name " \
                                            "has not yet been initialized"
        varnum = self.variable_names.index(name)

        res = _pyaccess.get_all_aggregate_accessibility_variables(distance,
                                                                  varnum,
                                                                  agg,
                                                                  decay,
                                                                  gno,
                                                                  imp_num)

        return pd.Series(res, index=self.node_ids)

    def get_node_ids(self, x_col, y_col, mapping_distance=-1):
        """
        Assign node_ids to data specified by x_col and y_col

        Parameters
        ----------
        x_col : Pandas series (float)
            A Pandas Series where values specify the x (e.g. longitude)
            location of dataset.
        y_col : Pandas series (float)
            A Pandas Series where values specify the y (e.g. latitude)
            location of dataset.  x_col and y_col should use the same index.
        mapping_distance : float, optional
            The maximum distance that will be considered a match between the
            x, y data and the nearest node in the network.  If not specified,
            every x, y coordinate will be mapped to the nearest node

        Returns
        -------
        node_ids : Pandas series (int)
            Returns a Pandas Series of node_ids for each x, y in the input data.
            The index is the same as the indexes of the x, y input data,
            and the values are the mapped node_ids. If mapping distance is
            not passed and if there are no nans in the x, y data, this will
            be the the same length as the x, y data.  If the mapping is
            imperfect, this function returns all the input x, y's that were
            successfully mapped to node_ids.
        """
        xys = pd.DataFrame({'x': x_col, 'y': y_col}).dropna(how='any')

        # no limit to the mapping distance
        node_ids = _pyaccess.xy_to_node(xys.astype('float32'),
                                        mapping_distance,
                                        self.graph_no)

        s = pd.Series(node_ids, index=xys.index)
        return s[s != -1]

    def plot(self, s, width=24, height=30, dpi=300,
             scheme_type="sequential", color='YlGn', numbins=7,
             bbox=None):
        """
        Experimental method to write the network to a matplotlib image.
        """
        df = pd.DataFrame({'xcol': self.nodes_df.x.values,
                           'ycol': self.nodes_df.y.values,
                           'zcol': s.values})

        if bbox is not None:
            df = df.query("xcol > %f and ycol > %f and xcol < %f and ycol < "
                          "%f" % tuple(bbox))

        plt.figure(num=None, figsize=(width, height), dpi=dpi, edgecolor='k')
        plt.scatter(df.xcol, df.ycol, c=df.zcol,
                    cmap=brewer2mpl.get_map(color, scheme_type, numbins).
                    mpl_colormap,
                    norm=matplotlib.colors.SymLogNorm(.01),
                    edgecolors='grey',
                    linewidths=0.1)

    def initialize_pois(self, numcategories, maxdist, maxitems):
        _pyaccess.initialize_pois(numcategories, maxdist, maxitems)

    def initialize_poi_category(self, category, xcol, ycol):
        if category not in CAT_NAME_TO_IND:
            CAT_NAME_TO_IND[category] = len(CAT_NAME_TO_IND)

        df = pd.concat([xcol, ycol], axis=1)
        print df.describe()

        _pyaccess.initialize_category(CAT_NAME_TO_IND[category],
                                      df.as_matrix().astype('float32'))

    def compute_nearest_pois(my, distance, category):
        assert category in CAT_NAME_TO_IND, "Category not initialized"

        return _pyaccess.find_all_nearest_pois(distance,
                                               CAT_NAME_TO_IND[category])