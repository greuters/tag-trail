# -*- coding: utf-8 -*-
#  tagtrail: A bundle of tools to organize a minimal-cost, trust-based and thus
#  time efficient accounting system for small, self-service community stores.
#
#  Copyright (C) 2019, Simon Greuter
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program.  If not, see <https://www.gnu.org/licenses/>.
from .context import database
from .context import helpers
from .context import sheets

import filecmp
import os
import random
import unittest

class TagtrailTestCase(unittest.TestCase):
    def create_active_test_product(self, db):
        """
        Create a new Product with expectedQuantity > 0 and an empty active
        input sheet

        :param db: database to add the product to and read configuration
        :type db: :class: `database.Database`
        :return: the new product and sheet
        :rtype: (:class: `database.Product`, :class: `sheets.ProductSheet`)
        """
        testProduct = database.Product('test product', 100, 'g', 12.3, .05, 50,
                addedQuantity = 0, soldQuantity = 0)
        db.products[testProduct.id] = testProduct
        sheet = self.generateProductSheet(db.config, testProduct, 1)
        sheet.store(f'{self.testRootDir}/0_input/sheets/active/')
        return (testProduct, sheet)

    def create_inactive_test_product(self, db):
        """
        Create a new Product with expectedQuantity <= 0 and an empty inactive
        input sheet

        :param db: database to add the product to and read configuration
        :type db: :class: `database.Database`
        :return: the new product and sheet
        :rtype: (:class: `database.Product`, :class: `sheets.ProductSheet`)
        """
        testProduct = database.Product('test product', 100, 'kg', 1.3, .05, -3,
                addedQuantity = 0, soldQuantity = 0)
        db.products[testProduct.id] = testProduct
        sheet = self.generateProductSheet(db.config, testProduct, 1)
        sheet.store(f'{self.testRootDir}/0_input/sheets/inactive/')
        return (testProduct, sheet)

    def add_tags_to_product_sheet(self, sheet, memberIds, maxNumTagsToAdd):
        """
        Add random tags to a product sheet

        :param sheet: sheet to add tags to
        :type sheet: :class: `sheets.ProductSheet`
        :param memberIds: list of memberIds to choose from
        :type memberIds: list of str
        :param maxNumTagsToAdd: how many tags to add at most. Actual number of
            tags added is smaller if not enough free data boxes are available
        :type maxNumTagsToAdd: int
        :return: dictionary {memberId -> numTagsAdded} of number of tags added per member
        :rtype: dict {str -> int}
        """
        tagsPerMember = {memberId: 0 for memberId in memberIds}
        numTagsAdded = 0
        for box in sheet.dataBoxes():
            if numTagsAdded == maxNumTagsToAdd:
                break
            if box.text != '':
                continue
            memberId = random.choice(memberIds)
            tagsPerMember[memberId] += 1
            box.text = memberId
            box.confidence = 1
            numTagsAdded += 1
        return tagsPerMember

    def generateProductSheet(self, config, product, sheetNumber):
        """
        Generate a new :class: `sheets.ProductSheet`

        :param config: configuration specifying currency and sheetNumber format
        :type config: :class: `configparser.ConfigParser`
        :param product: the product to generate a sheet for
        :type product: :class: `database.Product`
        :param sheetNumber: number of the sheet
        :type sheetNumber: int
        :return: a new product sheet
        :rtype: :class: `sheets.ProductSheet`
        """
        sheet = sheets.ProductSheet(helpers.Log(helpers.Log.LEVEL_ERROR))
        sheet.name = product.description
        sheet.amountAndUnit = product.amountAndUnit
        sheet.grossSalesPrice = helpers.formatPrice(
                product.grossSalesPrice(),
                config.get('general', 'currency'))
        sheet.sheetNumber = config.get('tagtrail_gen',
                'sheet_number_string').format(sheetNumber=str(sheetNumber))
        return sheet

    def check_sheets_in_dir(self, templateDir, testDir, excludedProductIds):
        """
        Check that all files in templateDir and testDir are equivalent and
        exist in both, apart from the excluded ones.

        :param templateDir: directory of the template sheets
        :type templateDir: str
        :param testDir: directory of the tested sheets
        :type testDir: str
        :param excludedProductIds: productIds to be excluded from the comparison
        :type excludedProductIds: list of str
        """
        self.check_files_in_dir(templateDir, testDir, lambda filename:
                sheets.ProductSheet.productId_from_filename(filename) not in
                excludedProductIds)

    def check_bills_in_dir(self, templateDir, testDir, excludedMemberIds):
        """
        Check that all bills in templateDir and testDir are equivalent and
        exist in both, apart from the excluded ones.

        :param templateDir: directory of the template sheets
        :type templateDir: str
        :param testDir: directory of the tested sheets
        :type testDir: str
        :param excludedMemberIds: memberIds to be excluded from the comparison
        :type excludedMemberIds: list of str
        """
        self.check_files_in_dir(templateDir, testDir,
                lambda filename: filename.split('.')[0] not in excludedMemberIds)

    def check_files_in_dir(self, templateDir, testDir, filenameFilter):
        """
        Check that all files in templateDir and testDir are equivalent and
        exist in both, apart from the ones excluded by filenameFilter.

        :param templateDir: directory of the template files
        :type templateDir: str
        :param testDir: directory of the tested files
        :type testDir: str
        :param filenameFilter: filter query to decide if a filename should be
            excluded (filenameFilter returns False) or not
        :type filenameFilter: function(str) -> bool
        """
        testedFilenames = os.listdir(testDir)

        # make sure no template files are missed, as filecmp only compares the
        # given testedFilenames
        for filename in os.listdir(templateDir):
            if not filenameFilter(filename):
                continue
            self.assertIn(filename, testedFilenames)

        testedFilenames = list(filter(filenameFilter, testedFilenames))

        match, mismatch, errors = filecmp.cmpfiles(
                templateDir, testDir, testedFilenames)
        self.assertEqual(len(errors), 0, errors)
        self.assertEqual(len(mismatch), 0, mismatch)
        self.assertEqual(len(match), len(testedFilenames),
                f'match: {match}, testedFilenames: {testedFilenames}')
