import unittest
import os
import json
import re
import sqlalchemy
import datetime

from ...common import CubesTestCaseBase
from sqlalchemy import Table, Column, Integer, Float, String, MetaData, ForeignKey
from sqlalchemy import create_engine
from cubes.backends.sql.mapper import coalesce_physical
from cubes.backends.sql.star import *

from cubes import *
from cubes.errors import *

class StarSQLTestCase(CubesTestCaseBase):
    def setUp(self):
        super(StarSQLTestCase, self).setUp()

        self.engine = sqlalchemy.create_engine('sqlite://')
        metadata = sqlalchemy.MetaData(bind=self.engine)

        table = Table('sales', metadata,
                        Column('id', Integer, primary_key=True),
                        Column('amount', Float),
                        Column('discount', Float),
                        Column('fact_detail1', String),
                        Column('fact_detail2', String),
                        Column('flag', String),
                        Column('date_id', Integer),
                        Column('product_id', Integer),
                        Column('category_id', Integer)
                    )

        table = Table('dim_date', metadata,
                        Column('id', Integer, primary_key=True),
                        Column('day', Integer),
                        Column('month', Integer),
                        Column('month_name', String),
                        Column('month_sname', String),
                        Column('year', Integer)
                    )

        table = Table('dim_product', metadata,
                        Column('id', Integer, primary_key=True),
                        Column('category_id', Integer),
                        Column('product_name', String),
                    )

        table = Table('dim_category', metadata,
                        Column('id', Integer, primary_key=True),
                        Column('category_name_en', String),
                        Column('category_name_sk', String),
                        Column('subcategory_id', Integer),
                        Column('subcategory_name_en', String),
                        Column('subcategory_name_sk', String)
                    )

        self.metadata = metadata
        self.metadata.create_all(self.engine)

        self.workspace = self.create_workspace({"engine":self.engine}, "sql_star_test.json")
        # self.workspace = Workspace()
        # self.workspace.register_default_store("sql", engine=self.engine)
        # self.workspace.add_model()
        self.cube = self.workspace.cube("sales")
        store = self.workspace.get_store("default")

        self.browser = SnowflakeBrowser(self.cube,store=store,
                                        dimension_prefix="dim_")
        self.browser.debug = True
        self.mapper = self.browser.mapper
        self.browser.logger.setLevel("DEBUG")


class QueryContextTestCase(StarSQLTestCase):
    def setUp(self):
        super(QueryContextTestCase, self).setUp()

    def test_denormalize(self):
        statement = self.browser.context.denormalized_statement()
        cols = [column.name for column in statement.columns]
        self.assertEqual(18, len(cols))

    def test_denormalize_locales(self):
        """Denormalized view should have all locales expanded"""
        statement = self.browser.context.denormalized_statement(expand_locales=True)
        cols = [column.name for column in statement.columns]
        self.assertEqual(20, len(cols))

    def test_levels_from_drilldown(self):
        cell = Cell(self.cube)
        dim = self.cube.dimension("date")
        l_year = dim.level("year")
        l_month = dim.level("month")
        l_day = dim.level("day")

        drilldown = [("date", None, "year")]
        result = levels_from_drilldown(cell, drilldown)
        self.assertEqual(1, len(result))

        dd = result[0]
        self.assertEqual(dim, dd.dimension)
        self.assertEqual(dim.hierarchy(), dd.hierarchy)
        self.assertSequenceEqual([l_year], dd.levels)
        self.assertEqual(["date.year"], dd.keys)

        # Try "next level"

        cut = PointCut("date", [2010])
        cell = Cell(self.cube, [cut])

        drilldown = [("date", None, "year")]
        result = levels_from_drilldown(cell, drilldown)
        self.assertEqual(1, len(result))
        dd = result[0]
        self.assertEqual(dim, dd.dimension)
        self.assertEqual(dim.hierarchy(), dd.hierarchy)
        self.assertSequenceEqual([l_year], dd.levels)
        self.assertEqual(["date.year"], dd.keys)

        drilldown = ["date"]
        result = levels_from_drilldown(cell, drilldown)
        self.assertEqual(1, len(result))
        dd = result[0]
        self.assertEqual(dim, dd.dimension)
        self.assertEqual(dim.hierarchy(), dd.hierarchy)
        self.assertSequenceEqual([l_year, l_month], dd.levels)
        self.assertEqual(["date.year", "date.month"], dd.keys)

        # Try with range cell

        # cut = RangeCut("date", [2009], [2010])
        # cell = Cell(self.cube, [cut])

        # drilldown = ["date"]
        # expected = [(dim, dim.hierarchy(), [l_year, l_month])]
        # self.assertEqual(expected, levels_from_drilldown(cell, drilldown))

        # drilldown = [("date", None, "year")]
        # expected = [(dim, dim.hierarchy(), [l_year])]
        # self.assertEqual(expected, levels_from_drilldown(cell, drilldown))

        # cut = RangeCut("date", [2009], [2010, 1])
        # cell = Cell(self.cube, [cut])

        # drilldown = ["date"]
        # expected = [(dim, dim.hierarchy(), [l_year, l_month, l_day])]
        # self.assertEqual(expected, levels_from_drilldown(cell, drilldown))

        # Try "last level"

        cut = PointCut("date", [2010, 1,2])
        cell = Cell(self.cube, [cut])

        drilldown = [("date", None, "day")]
        result = levels_from_drilldown(cell, drilldown)
        dd = result[0]
        self.assertSequenceEqual([l_year, l_month, l_day], dd.levels)
        self.assertEqual(["date.year", "date.month", "date.id"], dd.keys)

        drilldown = ["date"]
        result = levels_from_drilldown(cell, drilldown)
        dd = result[0]
        self.assertSequenceEqual([l_year, l_month], dd.levels)
        self.assertEqual(["date.year", "date.month"], dd.keys)


class JoinsTestCase(StarSQLTestCase):
    def setUp(self):
        super(JoinsTestCase, self).setUp()

        self.joins = [
                {"master":"fact.date_id", "detail": "date.id"},
                {"master":["fact", "product_id"], "detail": "product.id"},
                {"master":"fact.contract_date_id", "detail": "date.id", "alias":"contract_date"},
                {"master":"product.subcategory_id", "detail": "subcategory.id"},
                {"master":"subcategory.category_id", "detail": "category.id"}
            ]
        self.mapper._collect_joins(self.joins)

    def test_basic_joins(self):
        relevant = self.mapper.relevant_joins([[None,"date"]])
        self.assertEqual(1, len(relevant))
        self.assertEqual("date", relevant[0].detail.table)
        self.assertEqual(None, relevant[0].alias)

        relevant = self.mapper.relevant_joins([[None,"product","name"]])
        self.assertEqual(1, len(relevant))
        self.assertEqual("product", relevant[0].detail.table)
        self.assertEqual(None, relevant[0].alias)

    def test_alias(self):
        relevant = self.mapper.relevant_joins([[None,"contract_date"]])
        self.assertEqual(1, len(relevant))
        self.assertEqual("date", relevant[0].detail.table)
        self.assertEqual("contract_date", relevant[0].alias)

    def test_snowflake(self):
        relevant = self.mapper.relevant_joins([[None,"subcategory"]])

        self.assertEqual(2, len(relevant))
        test = sorted([r.detail.table for r in relevant])
        self.assertEqual(["product","subcategory"], test)
        self.assertEqual([None, None], [r.alias for r in relevant])

        relevant = self.mapper.relevant_joins([[None,"category"]])

        self.assertEqual(3, len(relevant))
        test = sorted([r.detail.table for r in relevant])
        self.assertEqual(["category", "product","subcategory"], test)
        self.assertEqual([None, None, None], [r.alias for r in relevant])


class StarValidationTestCase(StarSQLTestCase):
    @unittest.skip("not implemented")
    def test_validate(self):
        result = self.browser.validate_model()
        self.assertEqual(0, len(result))

class StarSQLBrowserTestCase(StarSQLTestCase):
    def setUp(self):
        super(StarSQLBrowserTestCase, self).setUp()
        fact = {
            "id":1,
            "amount":100,
            "discount":20,
            "fact_detail1":"foo",
            "fact_detail2":"bar",
            "flag":1,
            "date_id":20120308,
            "product_id":1,
            "category_id":10
        }

        date = {
            "id": 20120308,
            "day": 8,
            "month": 3,
            "month_name": "March",
            "month_sname": "Mar",
            "year": 2012
        }

        product = {
            "id": 1,
            "category_id": 10,
            "product_name": "Cool Thing"
        }

        category = {
            "id": 10,
            "category_id": 10,
            "category_name_en": "Things",
            "category_name_sk": "Veci",
            "subcategory_id": 20,
            "subcategory_name_en": "Cool Things",
            "subcategory_name_sk": "Super Veci"
        }

        ftable = self.table("sales")
        self.engine.execute(ftable.insert(), fact)
        table = self.table("dim_date")
        self.engine.execute(table.insert(), date)
        ptable = self.table("dim_product")
        self.engine.execute(ptable.insert(), product)
        table = self.table("dim_category")
        self.engine.execute(table.insert(), category)

        for i in range(1, 10):
            record = dict(product)
            record["id"] = product["id"] + i
            record["product_name"] = product["product_name"] + str(i)
            self.engine.execute(ptable.insert(), record)

        for j in range(1, 10):
            for i in range(1, 10):
                record = dict(fact)
                record["id"] = fact["id"] + i + j *10
                record["product_id"] = fact["product_id"] + i
                self.engine.execute(ftable.insert(), record)

    def table(self, name):
        return sqlalchemy.Table(name, self.metadata,
                                autoload=True)

    def test_get_fact(self):
        """Get single fact"""
        self.assertEqual(True, self.mapper.simplify_dimension_references)
        fact = self.browser.fact(1)
        self.assertIsNotNone(fact)
        self.assertEqual(18, len(fact.keys()))

    def test_get_members(self):
        """Get dimension values"""
        members = list(self.browser.members(None,"product",1))
        self.assertIsNotNone(members)
        self.assertEqual(1, len(members))

        members = list(self.browser.members(None,"product",2))
        self.assertIsNotNone(members)
        self.assertEqual(1, len(members))

        members = list(self.browser.members(None,"product",3))
        self.assertIsNotNone(members)
        self.assertEqual(10, len(members))

    def test_cut_details(self):
        cut = PointCut("date", [2012])
        details = self.browser.cut_details(cut)
        self.assertEqual([{"date.year":2012, "_key":2012, "_label":2012}], details)

        cut = PointCut("date", [2013])
        details = self.browser.cut_details(cut)
        self.assertEqual(None, details)

        cut = PointCut("date", [2012,3])
        details = self.browser.cut_details(cut)
        self.assertEqual([{"date.year":2012, "_key":2012, "_label":2012},
                          {"date.month_name":"March",
                          "date.month_sname":"Mar",
                          "date.month":3,
                          "_key":3, "_label":"March"}], details)

    @unittest.skip("test model is not suitable")
    def test_cell_details(self):
        cell = Cell(self.cube, [PointCut("date", [2012])])
        details = self.browser.cell_details(cell)
        self.assertEqual(1, len(details))

        cell = Cell(self.cube, [PointCut("product", [10])])
        details = self.browser.cell_details(cell)
        self.assertEqual(1, len(details))

    def test_aggregation_for_measures(self):
        context = self.browser.context

        aggs = context.aggregations_for_measure(self.cube.measure("amount"))
        self.assertEqual(2, len(aggs))

        aggs = context.aggregations_for_measure(self.cube.measure("discount"))
        self.assertEqual(1, len(aggs))

    def test_aggregate(self):
        result = self.browser.aggregate()
        keys = sorted(result.summary.keys())
        self.assertEqual(4, len(keys))
        self.assertEqual(["amount_min", "amount_sum", "discount_sum", "record_count"], keys)

        result = self.browser.aggregate(None, measures=["amount"])
        keys = sorted(result.summary.keys())
        self.assertEqual(2, len(keys))
        self.assertEqual(["amount_min", "amount_sum"], keys)

        result = self.browser.aggregate(None, measures=["discount"])
        keys = sorted(result.summary.keys())
        self.assertEqual(1, len(keys))
        self.assertEqual(["discount_sum"], keys)

    @unittest.skip("not implemented")
    def test_aggregate_measure_only(self):
        """Aggregation result should: SELECT from fact only"""
        pass

    @unittest.skip("not implemented")
    def test_aggregate_flat_dimension(self):
        """Aggregation result should SELECT from fact table onle, group by flat dimension attribute"""
        pass

    @unittest.skip("not implemented")
    def test_aggregate_joins(self):
        """Aggregation result should:
            * join date only - no other dimension joined
            * join all dimensions
            * snowflake join
        """
        pass

    @unittest.skip("not implemented")
    def test_aggregate_details(self):
        """Aggregation result should:
            details should be added after aggregation
            * fact details
            * dimension details
        """
        pass

    @unittest.skip("not implemented")
    def test_aggregate_join_date(self):
        """Aggregation result should join only date, no other joins should be performed"""
        pass


class HierarchyTestCase(CubesTestCaseBase):
    def setUp(self):
        super(HierarchyTestCase, self).setUp()

        engine = create_engine("sqlite:///")
        metadata = MetaData(bind=engine)
        d_table = Table("dim_date", metadata,
                        Column('id', Integer, primary_key=True),
                        Column('year', Integer),
                        Column('quarter', Integer),
                        Column('month', Integer),
                        Column('week', Integer),
                        Column('day', Integer))

        f_table = Table("ft_cube", metadata,
                        Column('id', Integer, primary_key=True),
                        Column('date_id', Integer))
        metadata.create_all()

        start_date = datetime.date(2000, 1, 1)
        end_date = datetime.date(2001, 1,1)
        delta = datetime.timedelta(1)
        date = start_date

        d_insert = d_table.insert()
        f_insert = f_table.insert()

        i = 1
        while date < end_date:
            record = {
                        "id": int(date.strftime('%Y%m%d')),
                        "year": date.year,
                        "quarter": (date.month-1)//3+1,
                        "month": date.month,
                        "week": int(date.strftime("%U")),
                        "day": date.day
                    }

            engine.execute(d_insert.values(record))

            # For each date insert one fact record
            record = {"id": i,
                      "date_id": record["id"]
                      }
            engine.execute(f_insert.values(record))
            date = date + delta
            i += 1

        workspace = self.create_workspace({"engine": engine},
                                          "hierarchy.json")
        self.cube = workspace.cube("cube")
        self.browser = SnowflakeBrowser(self.cube,
                                        store=workspace.get_store("default"),
                                        dimension_prefix="dim_",
                                        fact_prefix="ft_")
        self.browser.debug = True

    def test_cell(self):
        cell = Cell(self.cube)
        result = self.browser.aggregate(cell)
        self.assertEqual(366, result.summary["record_count"])

        cut = PointCut("date", [2000, 2])
        cell = Cell(self.cube, [cut])
        result = self.browser.aggregate(cell)
        self.assertEqual(29, result.summary["record_count"])

        cut = PointCut("date", [2000, 2], hierarchy="ywd")
        cell = Cell(self.cube, [cut])
        result = self.browser.aggregate(cell)
        self.assertEqual(7, result.summary["record_count"])

        cut = PointCut("date", [2000, 1], hierarchy="yqmd")
        cell = Cell(self.cube, [cut])
        result = self.browser.aggregate(cell)
        self.assertEqual(91, result.summary["record_count"])

    def test_drilldown(self):
        cell = Cell(self.cube)
        result = self.browser.aggregate(cell, drilldown=["date"])
        self.assertEqual(1, result.total_cell_count)

        result = self.browser.aggregate(cell, drilldown=["date:month"])
        self.assertEqual(12, result.total_cell_count)

        result = self.browser.aggregate(cell,
                                        drilldown=[("date", None, "month")])
        self.assertEqual(12, result.total_cell_count)

        result = self.browser.aggregate(cell,
                                        drilldown=[("date", None, "day")])
        self.assertEqual(366, result.total_cell_count)

        # Test year-quarter-month-day
        hier = self.cube.dimension("date").hierarchy("yqmd")
        result = self.browser.aggregate(cell,
                                        drilldown=[("date", "yqmd", "day")])
        self.assertEqual(366, result.total_cell_count)

        result = self.browser.aggregate(cell,
                                        drilldown=[("date", "yqmd", "quarter")])
        self.assertEqual(4, result.total_cell_count)

    def test_range_drilldown(self):
        cut = RangeCut("date", [2000, 1], [2000,3])
        cell = Cell(self.cube, [cut])
        result = self.browser.aggregate(cell, drilldown=["date"])
        # This should test that it does not drilldown on range
        self.assertEqual(1, result.total_cell_count)

    def test_implicit_level(self):
        cut = PointCut("date", [2000])
        cell = Cell(self.cube, [cut])

        result = self.browser.aggregate(cell, drilldown=["date"])
        self.assertEqual(12, result.total_cell_count)
        result = self.browser.aggregate(cell, drilldown=["date:month"])
        self.assertEqual(12, result.total_cell_count)

        result = self.browser.aggregate(cell,
                                        drilldown=[("date", None, "month")])
        self.assertEqual(12, result.total_cell_count)

        result = self.browser.aggregate(cell,
                                        drilldown=[("date", None, "day")])
        self.assertEqual(366, result.total_cell_count)

    def test_hierarchy_compatibility(self):
        cut = PointCut("date", [2000])
        cell = Cell(self.cube, [cut])

        with self.assertRaises(HierarchyError):
            self.browser.aggregate(cell, drilldown=[("date", "yqmd", None)])

        cut = PointCut("date", [2000], hierarchy="yqmd")
        cell = Cell(self.cube, [cut])
        result = self.browser.aggregate(cell,
                                        drilldown=[("date", "yqmd", None)])

        self.assertEqual(4, result.total_cell_count)

        cut = PointCut("date", [2000], hierarchy="yqmd")
        cell = Cell(self.cube, [cut])
        self.assertRaises(HierarchyError, self.browser.aggregate,
                            cell, drilldown=[("date", "ywd", None)])

        cut = PointCut("date", [2000], hierarchy="ywd")
        cell = Cell(self.cube, [cut])
        result = self.browser.aggregate(cell,
                                        drilldown=[("date", "ywd", None)])

        self.assertEqual(54, result.total_cell_count)


def suite():
    suite = unittest.TestSuite()
    suite.addTest(unittest.makeSuite(JoinsTestCase))
    suite.addTest(unittest.makeSuite(StarSQLBrowserTestCase))
    suite.addTest(unittest.makeSuite(StarValidationTestCase))
    suite.addTest(unittest.makeSuite(QueryContextTestCase))
    suite.addTest(unittest.makeSuite(HierarchyTestCase))

    return suite

