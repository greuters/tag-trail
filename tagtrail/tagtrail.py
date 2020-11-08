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

from . import helpers
from . import tagtrail_gen
from . import tagtrail_ocr
from . import tagtrail_sanitize
from . import tagtrail_bankimport
from . import tagtrail_account
from . import tagtrail_send


def checkPreconditions():
    correctionTransactions = database.Database.readCsv(
            self.accountingDataPath+'0_input/correctionTransactions.csv',
            database.CorrectionTransactionDict)


def main(accountingDir, accountingDate, accessCode, accountantName, password):
    # preconditions:
    # 1. gnucash_transactions_currentDate.csv exported from GnuCash
    #    => new members that are not in transactions are ok, highlight and ask
    #    user. they get 0 balance initially, but a correction transaction can
    #    be made
    #    => members that exist need to have the same balance, or a correction
    #    with reasoning is necessary
    #    => members that exist in transaction, but not in database are ok and
    #    left alone
    #    hints:
    #    * can sum up all transactions per member to get current balance
    #    * date needs to be today on the date tagtrail is run
    #    * export all transactions of Member accounts with standard settings
    # 2. scans
    # 4. products.csv:
    #    => need description, unit (validate, if not able to validate, warn
    #    user but ok. gCo2e cannot be calculated without), netprice
    #    => numUnits (=> mandatory for tagtrail_gen, determines how many sheets
    #    are created); also needed for inventory => update automagically, plus
    #    have one col for new inserted units
    #    => eaternity mandatory: warning wenn nicht gefüllt oder nicht zu
    #    mappen
    #    => entscheid: bestellrunde blatt von matthias beibehalten, beim
    #    rückmelden was geliefert wurde und was nicht muss man da nachschauen
    #    welche infos an dominique zu schicken sind
    # 5. warning if no accounted_products are given => means all product sheets
    #    are considered to be new
    # 6. configuration file for email, backup etc
    # 7. template files for emails, can be copied from previous if wanted
    # 8. bank_transactions_prevAccountingDate_(currentAccountingDate-1)
    #    transaction export from Postfinance, including total span since last
    #    accounting (but check if transactions lie in range afterwards as well)
    #    => Home/Account transactions/Search Options: seit letzter Accounting
    #    => Home/Zahlungskonto/Bewegungen
    #    Datum (inklusive),
    #    bis heute (exklusive, dh. vor einem Tag; korrekte Daten einblenden),
    #    Booking Details on,  auf show more klicken bis der button weg ist,
    #    dann export)
    # 9. date of new accounting = today

    # Erwartung Ablauf:
    # 1. Ende Abrechnung wird das aktuelle produkte.csv aus der
    # letzten Abrechnung in den nächsten Abrechnungsordner 'next' kopiert und
    # ordnerstruktur angelegt
    # 2. wann immer man will, trägt man einen neuen wert in die spalte 'Neu
    # geliefert' ein oder fügt eine Zeile hinzu (erwartung: alle spalten korrekt
    # befüllen). tagtrail_gen kann aus dem file neue Produktdatenblätter
    # generieren, auch unabhängig von der abrechnung (es macht einfach genug für
    # max(Bestand Inventur, Bestand letzte Abrechnung)+'Neu geliefert');
    # nutzer kann die ausdrucken die er will und in speicher legen
    # 3. wenn eine Abrechnung gemacht wird, kann vorher optional Inventur gemacht
    # werden: => Spalte Bestand Inventur im Abrechnungsordner 'next' anpassen.
    # wichtig: zum gleichen Zeitpunkt wie die Scans machen, damit das auch zusammenpasst
    # Bei der Abrechnung wird der neue erwartete Bestand berechnet, mit inventur
    # abgeglichen und der nutzer über differenzen informiert => zwei fehler transaktionen schreiben
    # => wurde keine Inventur gemacht, kommt ins next/produkte.csv die Spalte
    # 'Erwarteter Bestand am Datum', sonst kommt hier 'Geprüfter Bestand am
    # Datum' rein
    # 4. Differenzen bei der Bestellrunde werden neu in Matthias' File vermerkt,
    # und von dort aus mutationen an dominique gemeldet (inkl. produzent); ins
    # produkte.csv wird nur die spalte neu geliefert übertragen und alles andere
    # angepasst wo nötig

    # 1. go through all necessary input files, check if they are here and valid
    # if any file is missing, prompt the user to deliver them, including a
    # description how to do that
    #
    # if all files are present, invoke all other tagtrail programs until we are
    # done and ready for the next accounting
    # the whole program needs to be reentrant, asking if compute-heavy stuff
    # should be redone
    log = helpers.Log()
    log.info('Invoking tagtrail_gen')
    tagtrail_gen.main(accountingDir)
    log.info('Invoking tagtrail_ocr')
    tagtrail_ocr.main(accountingDir, 'data/tmp/')
    log.info('Invoking tagtrail_sanitize')
    tagtrail_sanitize.Gui(accountingDir)
    log.info('Invoking tagtrail_bankimport')
    tagtrail_bankimport.Gui(accountingDir, accountingDate)
    log.info('Invoking tagtrail_account')
    renamedAccountingDir = f'data/accounting_{accountingDate}/'
    tagtrail_account.main(accountingDir, renamedAccountingDir, accountingDate,
            'data/next/')
    log.info('Invoking tagtrail_send')
    tagtrail_send.MailSender(renamedAccountingDir,
            accessCode,
            accountantName,
            password).sendBills()

    # postprocessing:
    # lead user through additional manual steps they need to do

if __name__== "__main__":
    parser = argparse.ArgumentParser(
        description='Recognize tags on all input scans, storing them as CSV files')
    parser.add_argument('accountingDir',
            help='Top-level tagtrail directory to process, usually data/next/')
    parser.add_argument('--accountingDate',
            dest='accountingDate',
            type=helpers.DateUtility.strptime,
            default=helpers.DateUtility.todayStr(),
            help="Date of the new accounting, fmt='YYYY-mm-dd'",
            )
    parser.add_argument('accessCode',
            help="New access code to be sent to members",)
    parser.add_argument('accountantName',
            help="Name of the person responsible for this accounting.")
    parser.add_argument('-p, --password',
            dest='password',
            help=f'password for {tagtrail_send.MailSender.mailUser}. If not passed, it will be requested.')
    args = parser.parse_args()
    main(args.accountingDir, args.accountingDate, args.accessCode,
            args.accountantName, args.password)
