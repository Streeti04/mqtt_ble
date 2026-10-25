"""
BLE Diagnose-Tool für Govee-Sensoren
=====================================
Zeigt ALLE Daten an, die von BLE-Geräten empfangen werden.
Hilft bei der Diagnose von Problemen mit fehlenden Herstellerdaten.

Verwendung: python ble_diagnostic.py
Beenden: Strg+C
"""

import logging
from time import sleep
from bleson import get_provider, Observer
from datetime import datetime

# Logging-Konfiguration
logging.basicConfig(
    format='%(asctime)s.%(msecs)03d - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    level=logging.INFO)

# Zähler für Statistiken
stats = {
    'total_advertisements': 0,
    'govee_devices': set(),
    'govee_with_data': 0,
    'govee_without_data': 0
}

def on_advertisement(advertisement):
    """
    Zeigt detaillierte Informationen über jedes BLE-Advertisement
    """
    stats['total_advertisements'] += 1
    
    # Interessieren uns nur für Govee-Geräte
    if advertisement.name and 'GV' in advertisement.name:
        stats['govee_devices'].add(str(advertisement.address))
        
        print("\n" + "="*80)
        print(f"📡 GOVEE DEVICE GEFUNDEN - {datetime.now().strftime('%H:%M:%S.%f')[:-3]}")
        print("="*80)
        print(f"📱 Name:          {advertisement.name}")
        print(f"📍 MAC-Adresse:   {advertisement.address}")
        
        # RSSI (Signalstärke)
        if hasattr(advertisement, 'rssi') and advertisement.rssi:
            print(f"📶 RSSI:          {advertisement.rssi} dBm")
        
        # Manufacturer Data (Herstellerdaten) - WICHTIG für Sensordaten!
        mfg_data = advertisement.mfg_data
        if mfg_data:
            stats['govee_with_data'] += 1
            print(f"✅ MFG Data:      VORHANDEN ({len(mfg_data)} bytes)")
            print(f"   Hex:           {mfg_data.hex()}")
            print(f"   Bytes:         {list(mfg_data)}")
            
            # Versuche Daten zu dekodieren (Position 3-6 für die meisten Govee-Geräte)
            if len(mfg_data) >= 7:
                try:
                    values = int.from_bytes(mfg_data[3:6], 'big')
                    temp = float(values / 10000)
                    hum = float((values % 1000) / 10)
                    battery = mfg_data[6]
                    
                    print(f"🌡️  Temperatur:    {temp:.2f} °C")
                    print(f"💧 Luftfeuchte:   {hum:.1f} %")
                    print(f"🔋 Batterie:      {battery} %")
                except Exception as e:
                    print(f"⚠️  Dekodierung fehlgeschlagen: {e}")
        else:
            stats['govee_without_data'] += 1
            print(f"❌ MFG Data:      NICHT VORHANDEN")
        
        # Service Data (alternative Datenquelle)
        if hasattr(advertisement, 'service_data') and advertisement.service_data:
            print(f"📋 Service Data:  {advertisement.service_data}")
        
        # UUID Services
        if hasattr(advertisement, 'uuid16s') and advertisement.uuid16s:
            print(f"🔖 UUID16s:       {advertisement.uuid16s}")
        if hasattr(advertisement, 'uuid128s') and advertisement.uuid128s:
            print(f"🔖 UUID128s:      {advertisement.uuid128s}")
        
        # Flags
        if hasattr(advertisement, 'flags') and advertisement.flags:
            print(f"🚩 Flags:         {advertisement.flags}")
        
        # TX Power
        if hasattr(advertisement, 'tx_pwr_lvl') and advertisement.tx_pwr_lvl:
            print(f"⚡ TX Power:      {advertisement.tx_pwr_lvl}")
        
        print("="*80)
        
        # Zeige Statistik alle 10 Govee-Advertisements
        if (stats['govee_with_data'] + stats['govee_without_data']) % 10 == 0:
            print_stats()

def print_stats():
    """Zeigt Statistiken an"""
    print("\n" + "📊 STATISTIK " + "="*66)
    print(f"Gesamt Advertisements:        {stats['total_advertisements']}")
    print(f"Verschiedene Govee-Geräte:    {len(stats['govee_devices'])}")
    print(f"Govee MIT Herstellerdaten:    {stats['govee_with_data']}")
    print(f"Govee OHNE Herstellerdaten:   {stats['govee_without_data']}")
    if stats['govee_devices']:
        print(f"\nGefundene Geräte:")
        for device in sorted(stats['govee_devices']):
            print(f"  • {device}")
    print("="*80 + "\n")

# Hauptprogramm
print("\n" + "="*80)
print("🔍 BLE DIAGNOSE-TOOL FÜR GOVEE-SENSOREN")
print("="*80)
print("Dieses Tool zeigt ALLE empfangenen Daten von Govee BLE-Geräten an.")
print("💡 Tipp: Drücke Strg+C zum Beenden")
print("="*80 + "\n")

logging.info("Initialisiere Bluetooth...")
adapter = get_provider().get_adapter()
observer = Observer(adapter)
observer.on_advertising_data = on_advertisement

logging.info("Starte BLE-Scanner...")
observer.start()

try:
    while True:
        sleep(5)  # Alle 5 Sekunden kurze Pause
except KeyboardInterrupt:
    print("\n\n👋 Diagnose-Tool wird beendet...")
    observer.stop()
    print_stats()
    print("Auf Wiedersehen! 👋\n")
