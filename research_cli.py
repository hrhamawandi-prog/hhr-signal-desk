"""Collect public crypto research or compare a CSV for any research category."""
import argparse
import csv
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent/'core'))
import market_research
from operations import path
from storage import read_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collect',action='store_true')
    parser.add_argument('--export',choices=market_research.RESEARCH_MARKETS['crypto'])
    parser.add_argument('--output')
    args=parser.parse_args()
    if args.collect:
        import ccxt
        exchange=ccxt.okx({'enableRateLimit':True,'timeout':10000})
        exchange.load_markets()
        report=market_research.collect(exchange)
        for coin,value in report['markets'].items(): print(coin,value['status'])
    if args.export:
        if not args.output: parser.error('--output required for export')
        rows=read_json(path('candles/'+args.export+'.json'),[])
        if not rows: parser.error('No collected candles for this symbol')
        with open(args.output,'w',newline='') as stream:
            writer=csv.writer(stream); writer.writerow(['timestamp','open','high','low','close','volume'])
            writer.writerows(r[:6] for r in rows)
    if not args.collect and not args.export: parser.error('Choose --collect or --export')


if __name__=='__main__': main()
