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
import argparse
from abc import ABC, abstractmethod
import helpers
import gui_components
from sheets import ProductSheet
import database
import tkinter
from tkinter import messagebox
import itertools
import traceback
import datetime
import os
import shutil
import csv
import copy
import eaternity

class TagCollector(ABC):
    """
    TagCollector reads all sanitized product sheets and compares them to
    those of the last accounting. It collects all newly added tags per product.
    """
    skipCnt = 1
    csvDelimiter = ';'
    quotechar = '"'
    newline = ''

    def __init__(self,
            accountedProductsPath,
            currentProductsToAccountPath,
            accountingDate,
            db, log = helpers.Log()):
        self.log = log
        self.db = db
        self.accountedProductsPath = accountedProductsPath
        self.currentProductsToAccountPath = currentProductsToAccountPath
        self.accountingDate = accountingDate
        self.accountedSheets = self.loadProductSheets(self.accountedProductsPath)
        self.currentSheets = self.loadProductSheets(self.currentProductsToAccountPath)
        self.checkPageConsistency()
        self.informAboutPriceChanges()
        self.newTagsPerProduct = self.collectNewTagsPerProduct()

    def currentProductPagePaths(self):
        return [sheet.filePath for sheet in self.currentSheets.values()]

    def taggedGrossSalesPrice(self, productId):
        return self.__sheetGrossSalesPrice(productId, self.currentSheets)

    def __sheetGrossSalesPrice(self, productId, sheets):
        prices = [s.grossSalesPrice
                  for (pId, _), s in sheets.items()
                  if pId == productId]
        if prices:
            if len(set(prices)) != 1:
                raise AssertionError('should have exactly one price per ' +
                        f'product, but {productId} has {prices}')
            return prices[0]
        else:
            return None

    def __sheetAmountAndUnit(self, productId, sheets):
        amountAndUnits = [s.amountAndUnit
                for (pId, _), s in sheets.items()
                if pId == productId]
        if amountAndUnits:
            if len(set(amountAndUnits)) != 1:
                raise ValueError(f'{productId} pages have different ' +
                        f'amounts, {amountAndUnits}')
            return amountAndUnits[0]
        else:
            return None

    def numNewTags(self, productId, memberIds):
        if productId in self.newTagsPerProduct:
            tags = self.newTagsPerProduct[productId]
            numTags = sum([1 for tag in tags if tag in memberIds])
            self.log.debug(f'tags={tags}, productId={productId}, ' + \
                    f'memberIds={memberIds}, numTags={numTags}')
            return numTags
        else:
            return 0

    def newTags(self, productId, pageNumber):
        key = (productId, pageNumber)
        accountedTags = self.accountedSheets[key].confidentTags()
        currentTags = self.currentSheets[key].confidentTags()
        assert(len(accountedTags) == len(currentTags))
        self.log.debug(f'accountedTags: {accountedTags}')
        self.log.debug(f'currentTags: {currentTags}')

        changedIndices = [idx for idx, tag in enumerate(accountedTags)
                if currentTags[idx] != tag]
        self.log.debug(f'changedIndices: {changedIndices}')
        offendingDataboxes = [f'dataBox{idx}' for idx in changedIndices if accountedTags[idx] != '']
        if offendingDataboxes:
            self.log.error(f'offendingDataboxes: {offendingDataboxes}')
            raise ValueError(
                'Already accounted tags were changed in the ' + \
                'current accounting.\n\n' + \
                'This situation indicates a tagging error and has ' + \
                'to be resolved manually by correcting this file:\n\n' + \
                f'{self.currentProductsToAccountPath}{productId}_{pageNumber}.csv\n\n' + \
                'Offending data boxes:\n\n' + \
                f'{offendingDataboxes}\n\n' + \
                'Corresponding file from last accounting:\n\n' + \
                f'{self.accountedProductsPath}{productId}_{pageNumber}.csv')

        return list(map(lambda idx: currentTags[idx], changedIndices))

    def collectNewTagsPerProduct(self):
        newTags = {}
        for key in self.currentSheets.keys():
            if key not in self.accountedSheets:
                newTags[key] = self.currentSheets[key].confidentTags()
            else:
                newTags[key] = self.newTags(key[0], key[1])

            unknownTags = list(filter(
                    lambda tag: tag and tag not in self.db.members.keys(),
                    newTags[key]))
            if unknownTags:
                raise Exception(
                    f"{self.currentProductsToAccountPath}{key[0]}_{key[1]}.csv " + \
                    f"contains a tag for non-existent members '{unknownTags}'." + \
                    "Run tagtrail_sanitize before tagtrail_account!")
            self.log.debug(f'newTags[{key}]={newTags[key]}')

        newTagsPerProduct = {}
        for productId, pageNumber in self.currentSheets.keys():
            if productId not in newTagsPerProduct:
                newTagsPerProduct[productId] = []
            newTagsPerProduct[productId] += newTags[productId, pageNumber]
        self.log.debug(f'newTagsPerProduct: {newTagsPerProduct.items()}')
        return newTagsPerProduct

    def loadProductSheets(self, path):
        self.log.info(f'collecting tags in {path}')
        csvFilePaths = helpers.sortedFilesInDir(path, ext = '.csv')
        if not csvFilePaths:
            return {}

        productSheets = {}
        for filePath in csvFilePaths:
            productId, pageNumber = os.path.splitext(filePath)[0].split('_')
            self.log.debug(f'productId={productId}, pageNumber={pageNumber}')
            sheet = ProductSheet()
            sheet.load(path+filePath)
            sheet.filePath = filePath
            if productId not in self.db.products:
                raise ValueError(f'{productId} has a sheet, but is ' +
                        'missing in database')
            if sheet.productId() != productId:
                raise ValueError(f'{path+filePath} is invalid.\n' +
                    '{sheet.productId()} != {productId}')
            if sheet.pageNumber != pageNumber:
                raise ValueError(f'{path+filePath} is invalid.\n' +
                    '{sheet.pageNumber()} != {pageNumber}')
            if sheet.unconfidentTags():
                raise ValueError(
                    f'{path+filePath} is not properly sanitized.\n' +
                    'Run tagtrail_sanitize before tagtrail_account!')
            if (productId, pageNumber) in productSheets:
                raise ValueError(
                    f'{(productId, pageNumber)} exists more than once')
            productSheets[(productId, pageNumber)] = sheet
        return productSheets

    def checkPageConsistency(self):
        """
        All pages of one product (current and already accounted ones) must have
        the same amount and price. Check and abort if this is not the case.
        """
        for productId in self.db.products.keys():
            accountedPrice = self.__sheetGrossSalesPrice(productId,
                    self.accountedSheets)

            currentPrice = self.__sheetGrossSalesPrice(productId,
                    self.accountedSheets)
            if (accountedPrice is not None
                    and currentPrice is not None
                    and accountedPrice != currentPrice):
                raise ValueError(f'{productId}: already accounted pages have '
                        + 'another price then current ones'
                        + f'({accountedPrice} != {currentPrice})')

            accountedAmount = self.__sheetAmountAndUnit(
                    productId,
                    self.accountedSheets)

            currentAmount = self.__sheetAmountAndUnit(productId,
                    self.accountedSheets)
            if (accountedAmount is not None
                    and currentAmount is not None
                    and accountedAmount != currentAmount):
                raise ValueError(f'{productId}: already accounted pages have '
                        + 'another amount then current ones'
                        + f'({accountedAmount} != {currentAmount})')

    def informAboutPriceChanges(self):
        for product in self.db.products.values():
            if (self.taggedGrossSalesPrice(product.id) !=
                    product.grossSalesPrice()):
                self.log.info(f'price of {product.id} changed from '+
                        f'{self.taggedGrossSalesPrice(product.id)} to ' +
                        f'{product.grossSalesPrice()}')

class Gui:
    def __init__(self, accountingDataPath, nextAccountingDataPath,
            accountingDate, configFilePath, updateCo2Statistics):
        self.log = helpers.Log()
        self.accountingDate = accountingDate
        self.accountingDataPath = accountingDataPath
        self.nextAccountingDataPath = nextAccountingDataPath

        self.root = tkinter.Tk()
        self.root.report_callback_exception = self.reportCallbackException
        self.root.geometry(str(self.root.winfo_screenwidth())+'x'+str(self.root.winfo_screenheight()))

        self.db = EnrichedDatabase(accountingDataPath, accountingDate,
                configFilePath, updateCo2Statistics)

        self.productSheetSelection = gui_components.Checkbar(self.root,
                'Accounted pages to keep:',
                self.db.productPagePaths, True)
        self.productSheetSelection.pack(side=tkinter.TOP, fill=tkinter.BOTH, expand=tkinter.YES, padx=5, pady=5)
        self.productSheetSelection.config(relief=tkinter.GROOVE, bd=2)

        accountedProducts = [path.split('_')[0] for path in self.db.productPagePaths]
        missingProducts = sorted([
            p.id for p in self.db.products.values()
            if p.id not in accountedProducts
            ])
        mp = gui_components.Checkbar(self.root, 'Missing products:', missingProducts, False)
        mp.pack(side=tkinter.TOP, fill=tkinter.BOTH, expand=tkinter.YES, padx=5, pady=5)
        mp.config(relief=tkinter.GROOVE, bd=2)

        buttonFrame = tkinter.Frame(self.root)
        buttonFrame.pack(side=tkinter.BOTTOM, pady=5)
        cancelButton = tkinter.Button(buttonFrame, text='Cancel',
                command=self.root.quit)
        cancelButton.pack(side=tkinter.LEFT)
        cancelButton.bind('<Return>', lambda _: self.root.quit())
        saveButton = tkinter.Button(buttonFrame, text='Save and Quit',
                command=self.saveAndQuit)
        saveButton.pack(side=tkinter.RIGHT)
        saveButton.bind('<Return>', lambda _: self.saveAndQuit())
        saveButton.focus_set()
        self.root.mainloop()

    def reportCallbackException(self, exception, value, tb):
        traceback.print_exception(exception, value, tb)
        messagebox.showerror('Abort Accounting', value)

    def saveAndQuit(self):
        try:
            self.writeBills()
            self.writeGnuCashFiles()
            self.prepareNextAccounting()
        finally:
            self.root.quit()

    def writeBills(self):
        destPath = f'{self.accountingDataPath}3_bills/'
        helpers.recreateDir(destPath, self.log)
        for member in self.db.members.values():
            self.db.writeCsv(destPath+member.id+'.csv',
                    self.db.bills[member.id])

    def writeGnuCashFiles(self):
        destPath = f'{self.accountingDataPath}/4_gnucash/'
        self.db.writeCsv(f'{destPath}accounts.csv', self.db.accounts)
        transactions = database.GnucashTransactionList(
                self.db.config,
                itertools.chain(self.db.purchaseTransactions,
                self.db.inventoryDifferenceTransactions))
        self.db.writeCsv(f'{destPath}gnucashTransactions.csv',
                transactions)

    def prepareNextAccounting(self):
        helpers.recreateDir(self.nextAccountingDataPath, self.log)
        helpers.recreateDir(f'{self.nextAccountingDataPath}0_input', self.log)
        helpers.recreateDir(f'{self.nextAccountingDataPath}0_input/scans', self.log)
        self.writeMemberCSV()
        self.writeProductsCSVs()
        self.copyAccountedSheets()
        self.db.writeCsv(f'{self.nextAccountingDataPath}0_input/correctionTransactions.csv',
                database.CorrectionTransactionDict(self.db.config))
        shutil.copytree(f'{self.accountingDataPath}0_input/templates',
                f'{self.nextAccountingDataPath}0_input/templates')

    def writeMemberCSV(self):
        newMembers = copy.deepcopy(self.db.members)
        newMembers.accountingDate = self.accountingDate
        for m in newMembers.values():
            m.balance = self.db.bills[m.id].currentBalance()
        self.db.writeCsv(f'{self.nextAccountingDataPath}0_input/members.csv',
                newMembers)

    def writeProductsCSVs(self):
        self.db.writeCsv(f'{self.accountingDataPath}5_output/products.csv',
                self.db.products)
        self.db.writeCsv(f'{self.nextAccountingDataPath}0_input/products.csv',
                self.db.products.copyForNextAccounting(self.accountingDate))

    def copyAccountedSheets(self):
        productSheetsToKeep = [path for selected, path in
                zip(self.productSheetSelection.state(),
                    self.db.productPagePaths)
                if selected == 1]
        self.log.debug(f'productSheetsToKeep = {productSheetsToKeep}')

        destPath = self.nextAccountingDataPath+'0_input/accounted_products/'
        helpers.recreateDir(destPath, self.log)
        for productFileName in productSheetsToKeep:
            srcPath = self.accountingDataPath+'2_taggedProductSheets/'+productFileName
            self.log.info("copy {} to {}".format(srcPath, destPath))
            shutil.copy(srcPath, destPath)

class EnrichedDatabase(database.Database):
    def __init__(self, accountingDataPath, accountingDate, configFilePath, updateCo2Statistics):
        self.log = helpers.Log()
        self.accountingDataPath = accountingDataPath
        self.accountingDate = accountingDate
        super().__init__(f'{accountingDataPath}0_input/', configFilePath=configFilePath)

        if updateCo2Statistics:
            api = eaternity.EaternityApi(self.config)
            for product in self.products.values():
                try:
                    gCo2e = api.co2Value(product)
                    if product.gCo2e != gCo2e:
                        self.log.info(f'Updated gCo2e from {product.gCo2e} '
                                +f'to {gCo2e} for {product.id}')
                        product.gCo2e = gCo2e
                    else:
                        self.log.debug(f'gCo2e for {product.id} = {gCo2e}')

                except ValueError:
                    self.log.info(f'Failed to retrieve gCo2e for {product.id}')

        tagCollector = TagCollector(
                self.accountingDataPath+'0_input/accounted_products/',
                self.accountingDataPath+'2_taggedProductSheets/',
                self.accountingDate,
                self, self.log)

        self.products.expectedQuantityDate = self.accountingDate
        for productId, product in self.products.items():
            product.soldQuantity = tagCollector.numNewTags(productId,
                    list(self.members.keys()))

        self.correctionTransactions = self.readCsv(
                self.accountingDataPath+'0_input/correctionTransactions.csv',
                database.CorrectionTransactionDict)
        self.paymentTransactions = self.loadPaymentTransactions()
        self.bills = self.createBills(tagCollector)
        self.productPagePaths = tagCollector.currentProductPagePaths()
        self.inventoryDifferenceTransactions = self.createInventoryDifferenceTransactions()
        self.purchaseTransactions = self.createPurchaseTransactions()
        self.accounts = database.MemberAccountDict(self.config,
                **{m.id: database.MemberAccount(m.id) for m in self.members.values()})

    def loadPaymentTransactions(self):
        toDate = self.accountingDate-datetime.timedelta(days=1)
        unprocessedTransactionsPath = self.accountingDataPath + \
                '5_output/unprocessed_Transactions_' + \
                helpers.DateUtility.strftime(self.previousAccountingDate) + '_' + \
                helpers.DateUtility.strftime(toDate) + '.csv'

        if not os.path.isfile(unprocessedTransactionsPath):
            raise Exception(
                f"{unprocessedTransactionsPath} does not exist.\n" + \
                "Run tagtrail_bankimport before tagtrail_account!")

        unprocessedPayments = [t.notificationText for t in
                self.readCsv(unprocessedTransactionsPath,
                    database.PostfinanceTransactionList)
                 if not t.creditAmount is None]
        if unprocessedPayments != []:
            messagebox.showwarning('Unprocessed payments exist',
                'Following payments will not be documented for our members:\n\n'
                + '\n\n'.join(unprocessedPayments) + '\n\n'
                + 'Run tagtrail_bankimport again if you want to correct this.')

        return self.readCsv(
                self.accountingDataPath+'4_gnucash/paymentTransactions.csv',
                database.GnucashTransactionList)

    def createBills(self, tagCollector):
        bills = {}
        for member in self.members.values():
            bill = database.Bill(self.config,
                    member.id,
                    self.members.accountingDate,
                    self.accountingDate,
                    member.balance,
                    sum([transaction.amount for transaction in
                        self.paymentTransactions
                        if transaction.sourceAccount == member.id]),
                    self.correctionTransactions[member.id].amount if member.id in self.correctionTransactions else 0,
                    self.correctionTransactions[member.id].justification if member.id in self.correctionTransactions else '')
            for productId in tagCollector.newTagsPerProduct.keys():
                numTags = tagCollector.numNewTags(productId, [member.id])
                if numTags != 0:
                    taggedGrossSalesPrice = tagCollector.taggedGrossSalesPrice(productId)
                    assert(taggedGrossSalesPrice is not None)
                    position = database.BillPosition(productId,
                            self.products[productId].description,
                            numTags,
                            self.products[productId].purchasePrice,
                            taggedGrossSalesPrice,
                            self.products[productId].gCo2e)
                    bill[position.id] = position
            bills[member.id] = bill
        return bills

    def createInventoryDifferenceTransactions(self):
        transactions = database.GnucashTransactionList(self.config)
        if not self.products.inventoryQuantityDate:
            self.log.info(
                'No inventoryQuantityDate given - not checking inventory')
            self.log.debug([p for p in self.products.values() if p.inventoryQuantity != 0])
            if [p for p in self.products.values() if p.inventoryQuantity != 0]:
                raise AssertionException('Add an inventoryQuantityDate ' +
                        'or omit inventory quantities alltogether')
            return transactions

        inventoryDifference = self.config.get('tagtrail_account',
                'inventory_difference')
        inventoryDifferenceAccount = self.config.get('tagtrail_account',
                'inventory_difference_account')
        for product in self.products.values():
            expectedQuantity = (product.previousQuantity
                    - product.soldQuantity
                    + product.addedQuantity)
            if product.inventoryQuantity < expectedQuantity:
                self.log.debug(f'{product.id}: expected = {expectedQuantity}, ' +
                        f'inventory = {product.inventoryQuantity}')
                quantityDifference = expectedQuantity - product.inventoryQuantity
                purchasePriceDifference = quantityDifference * product.purchasePrice
                grossSalesPriceDifference = quantityDifference * product.grossSalesPrice()
                transactions.append(database.GnucashTransaction(
                    f'{product.id}: {inventoryDifference} accounted on {self.accountingDate}',
                    purchasePriceDifference,
                    self.config.get('tagtrail_account',
                        'merchandise_value_account'),
                    inventoryDifferenceAccount,
                    self.accountingDate
                    ))
                transactions.append(database.GnucashTransaction(
                    f'{product.id}: {inventoryDifference} accounted on {self.accountingDate}',
                    grossSalesPriceDifference - purchasePriceDifference,
                    self.config.get('tagtrail_account', 'margin_account'),
                    inventoryDifferenceAccount,
                    self.accountingDate
                    ))

            elif product.inventoryQuantity > expectedQuantity:
                self.log.debug(f'{product.id}: expected = {expectedQuantity}, ' +
                        f'inventory = {product.inventoryQuantity}')
                quantityDifference = product.inventoryQuantity - expectedQuantity
                for bill in self.bills.values():
                    if not product.id in bill:
                        continue
                    if quantityDifference == 0:
                        break

                    billPos = bill[product.id]
                    billPos.numInventoryDifferenceTags = min(billPos.numTags, quantityDifference)
                    transactions.append(database.GnucashTransaction(
                        (f'{product.id}: {inventoryDifference} ' +
                            f'accounted on {self.accountingDate}'),
                        billPos.grossSalesPriceInventoryDifference(),
                        inventoryDifferenceAccount,
                        bill.memberId,
                        self.accountingDate
                        ))
                    quantityDifference -= billPos.numInventoryDifferenceTags
                if quantityDifference != 0:
                    raise AssertionError('quantityDifference != 0')
        return transactions

    def createPurchaseTransactions(self):
        merchandiseValue = self.config.get('tagtrail_account',
                'merchandise_value')
        merchandiseValueAccount = self.config.get('tagtrail_account',
                'merchandise_value_account')
        margin = self.config.get('tagtrail_account', 'margin')
        marginAccount = self.config.get('tagtrail_account', 'margin_account')
        return database.GnucashTransactionList(
                self.config,
                ([database.GnucashTransaction(
                    f'{merchandiseValue} accounted on {self.accountingDate}',
                    bill.purchasePriceWithoutInventoryDifference(),
                    merchandiseValueAccount,
                    bill.memberId,
                    self.accountingDate) for bill in self.bills.values()]
                +
                [database.GnucashTransaction(
                    f'{margin} accounted on {self.accountingDate}',
                    bill.grossSalesPriceWithoutInventoryDifference()-bill.purchasePriceWithoutInventoryDifference(),
                    marginAccount,
                    bill.memberId,
                    self.accountingDate) for bill in self.bills.values()])
                )

    @property
    def previousAccountingDate(self):
        return self.members.accountingDate

def main(accountingDir, renamedAccountingDir, accountingDate,
        nextAccountingDir, configFilePath, updateCo2Statistics):
    newDir = renamedAccountingDir.format(accountingDate = accountingDate)
    if accountingDir != newDir:
        shutil.move(accountingDir, newDir)
    Gui(newDir, nextAccountingDir, accountingDate, configFilePath, updateCo2Statistics)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Load payments and tagged product sheets to create a ' + \
            'bill for each member, provide transaction files ready to be ' + \
            'imported to GnuCash and prepare for the next accounting.')
    parser.add_argument('accountingDir',
            help='Top-level tagtrail directory to process, usually data/next/')
    parser.add_argument('--accountingDate',
            dest='accountingDate',
            type=helpers.DateUtility.strptime,
            default=helpers.DateUtility.todayStr(),
            help="Date of the new accounting, fmt='YYYY-mm-dd'",
            )
    parser.add_argument('--renamedAccountingDir',
            dest='renamedAccountingDir',
            default='data/accounting_{accountingDate}/',
            help="New name to rename accountingDir to. {accountingDate} " + \
                 "will be replaced by the value of the 'accountingDate' argument.")
    parser.add_argument('--nextAccountingDir',
            dest='nextAccountingDir',
            default='data/next/',
            help='Name of the top-level tagtrail directory to be created for the next accounting.')
    parser.add_argument('--configFilePath',
            dest='configFilePath',
            default='config/tagtrail.cfg',
            help='Path to the config file to be used.')
    parser.add_argument('--updateCo2Statistics',
            action='store_true',
            default=False,
            help='Retrieve new gCo2e statistics from eaternity')

    args = parser.parse_args()
    main(args.accountingDir, args.renamedAccountingDir, args.accountingDate,
            args.nextAccountingDir, args.configFilePath, args.updateCo2Statistics)
