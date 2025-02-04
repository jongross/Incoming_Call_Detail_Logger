## convert file to python 3 compatible, and reformat to pass a python lint check

##------------------------------------------
##--- Author: Pradeep Singh
##--- Blog: https://iotbytes.wordpress.com/incoming-call-details-logger-with-raspberry-pi/
##--- Date: 12 Jan 2018
##--- Version: 1.0
##--- Python Ver: 2.7
##--- Description: This python code will log all the incoming call details in MySQL DB using an analog Modem connected with Raspberry Pi
##------------------------------------------



import serial
import threading
import atexit
from datetime import datetime
import subprocess
import sqlite3
import os.path
from flask import Flask, request, session, g, redirect, url_for, \
     abort, render_template, flash, send_from_directory, jsonify
from werkzeug.local import LocalProxy
import sys


#=================================================================
# Global Variables
#=================================================================  

# Global Modem Object
analog_modem = serial.Serial()

# Used in global event listener
disable_modem_event_listener = True

# SQLite3 DB to Store Call Details
DB_NAME = 'call_log.db'

# DyanmoDB Table Name
DYNAMODB_TABLE_NAME = 'phonehome'

# default Modem ID (this is from a zoom model 3095 USB 56k modem)
MODEM_ID = 'CX93001-EIS_V0.2002-V92'

# ModemID AT CMD
# WARNING: This is specific to the zoom 3095, other modems will have different AT commands
MODEM_ID_AT_CMD = "ATI3"

#=================================================================



#=================================================================
# Set COM Port settings
#=================================================================
def set_COM_port_settings(com_port):
    analog_modem.port = com_port
    analog_modem.baudrate = 57600 #9600
    analog_modem.bytesize = serial.EIGHTBITS #number of bits per bytes
    analog_modem.parity = serial.PARITY_NONE #set parity check: no parity
    analog_modem.stopbits = serial.STOPBITS_ONE #number of stop bits
    analog_modem.timeout = 3            #non-block read
    analog_modem.xonxoff = False     #disable software flow control
    analog_modem.rtscts = False     #disable hardware (RTS/CTS) flow control
    analog_modem.dsrdtr = False      #disable hardware (DSR/DTR) flow control
    analog_modem.writeTimeout = 3     #timeout for write
#=================================================================



#=================================================================
# Detect Modem COM Port
#=================================================================
def detect_COM_port():

    # List all the Serial COM Ports on Raspberry Pi
    proc = subprocess.Popen(['ls /dev/tty[A-Za-z]*'], shell=True, stdout=subprocess.PIPE)
    com_ports = proc.communicate()[0].decode('utf-8')
    com_ports_list = com_ports.split('\n')

    # Find the right port associated with the Voice Modem
    for com_port in com_ports_list:
        if 'tty' in com_port:
            # Try to open the COM Port and execute AT Command
            try:
                # Set the COM Port Settings
                set_COM_port_settings(com_port)
                analog_modem.open()
            except Exception as e:
                print("Unable to open COM Port: " + com_port)
                print(e)
                pass
            else:
                # Try to put Modem in Voice Mode
                if not exec_AT_cmd("AT+FCLASS=8", "OK"):
                    print("Error: Failed to put modem into voice mode.")
                    if analog_modem.isOpen():
                        analog_modem.close()
                else:
                    # Found the COM Port exit the loop
                    print("Modem COM Port is: " + com_port)
                    analog_modem.flushInput()
                    analog_modem.flushOutput()
                    break
#=================================================================



#=================================================================
# Initialize Modem
#=================================================================
def init_modem_settings():
    
    # Detect and Open the Modem Serial COM Port
    try:
        detect_COM_port()
    except Exception as e:
        print("Error: Unable to open the Serial Port.")
        print(e)
        sys.exit()

    # Initialize the Modem
    try:
        # Flush any existing input output data from the buffers
        analog_modem.flushInput()
        analog_modem.flushOutput()
            
        # Test Modem connection, using basic AT command.
        if not exec_AT_cmd("AT"):
            print("Error: Unable to access the Modem")

        # Reset to factory default.
        if not exec_AT_cmd("AT&F"):
            print("Error: Unable reset to factory default")          

        modemid = exec_AT_cmd(MODEM_ID_AT_CMD)
        if modemid == False:
            print("Error: Unable to get Modem ID")
        else:
            MODEM_ID = modemid

        # Display result codes in verbose form  
        if not exec_AT_cmd("ATV1"):
            print("Error: Unable set response in verbose form")  

        # Enable Command Echo Mode.
        if not exec_AT_cmd("ATE1"):
            print("Error: Failed to enable Command Echo Mode")       

        # Enable formatted caller report.
        if not exec_AT_cmd("AT+VCID=1"):
            print("Error: Failed to enable formatted caller report.")
            
        # Flush any existing input outout data from the buffers
        analog_modem.flushInput()
        analog_modem.flushOutput()

    except Exception as e:
        print("Error: unable to Initialize the Modem")
        print(e)
        sys.exit()
#=================================================================




#=================================================================
# Execute AT Commands at the Modem
#=================================================================
def exec_AT_cmd(modem_AT_cmd, expected_response="OK"):
    
    global disable_modem_event_listener
    disable_modem_event_listener = True
    
    try:
        # Send command to the Modem
        analog_modem.write((modem_AT_cmd + "\r").encode())
        # Read Modem response
        execution_status = read_AT_cmd_response(expected_response)
        disable_modem_event_listener = False
        # Return command execution status
        return execution_status

    except Exception as e:
        disable_modem_event_listener = False
        print("Error: Failed to execute the command")
        print(e)
        return False        
#=================================================================



#=================================================================
# Read AT Command Response from the Modem
#=================================================================
def read_AT_cmd_response(expected_response="OK"):
    
    # Set the auto timeout interval
    start_time = datetime.now()

    MODEM_RESPONSE_READ_TIMEOUT = 10 # Tine in Seconds
    # TODO: when asked for the modemID, we need to capture it and send it to dynamoDB
    
    try:
        while True:
            # Read Modem Data on Serial Rx Pin
            modem_response = analog_modem.readline().decode('utf-8')
            print(modem_response)
            # Recieved expected Response
            if expected_response == modem_response.strip(' \t\n\r' + chr(16)):
                # this is a little hacky, but we need to capture the modemID, and string values are "truthy"...
                return modem_response.strip(' \t\n\r' + chr(16))
            # Failed to execute the command successfully
            elif "ERROR" in modem_response.strip(' \t\n\r' + chr(16)):
                return False
            # Timeout
            elif (datetime.now()-start_time).seconds > MODEM_RESPONSE_READ_TIMEOUT:
                return False

    except Exception as e:
        print("Error in read_modem_response function...")
        print(e)
        return False
#=================================================================



#=================================================================
# Global Data Listener
#=================================================================
def monitor_modem_line():
    
    global disable_modem_event_listener

    # Call detail dictionary
    call_record = {}

    while True:
        if not disable_modem_event_listener:
            modem_data = analog_modem.readline().decode('utf-8')
            
            if modem_data != "":
                print(modem_data)

                if ("DATE" in modem_data):
                    call_record['DATE'] = (modem_data[5:]).strip(' \t\n\r')
                if ("TIME" in modem_data):
                    call_record['TIME'] = (modem_data[5:]).strip(' \t\n\r')
                if ("NMBR" in modem_data):
                    call_record['NMBR'] = (modem_data[5:]).strip(' \t\n\r')
                    # Call call details logger
                    print(call_record)
                    call_details_logger(call_record)

                if "RING" in modem_data.strip(chr(16)):
                    pass
#=================================================================



#=================================================================
# Close the Serial Port
#=================================================================
def close_modem_port():
    # Close the Serial COM Port
    try:
        if analog_modem.isOpen():
            analog_modem.close()
            print ("Serial Port closed...")
    except Exception as e:
        print("Error: Unable to close the Serial Port.")
        print(e)
        sys.exit()
#=================================================================



#=================================================================
####################
#  FLASK WEB APP
####################
#=================================================================

# create application
app = Flask(__name__)
app.config.from_object(__name__)


#=================================================================
# Initialize SQLite3 Database
#=================================================================
def init_call_history_DB():
    
    table_init_sql = """drop table if exists Call_Details;

                        create table Call_Details (
                         S_No integer primary key autoincrement,
                         Phone_Number text,
                         Modem_Date text,
                         Modem_Time text,
                         System_Date_Time text,
                         Name text
                        );"""

    #Connect or Create DB File
    conn = sqlite3.connect(DB_NAME)
    curs = conn.cursor()

    #Create Tables
    sqlite3.complete_statement(table_init_sql)
    curs.executescript(table_init_sql)

    #Close DB
    curs.close()
    conn.close()

    print("SQLite3 Database initialized successfully")
#=================================================================



#=================================================================
# Save Call Details in Database
#=================================================================
def call_details_logger(call_record):
    with app.app_context():
        query = 'INSERT INTO Call_Details(Phone_Number, Modem_Date, Modem_Time, System_Date_Time, Name) VALUES(?,?,?,?,?)'
        arguments = [
            call_record['NMBR'],
            datetime.strptime(call_record['DATE'],'%m%d').strftime('%d-%b'),
            datetime.strptime(call_record['TIME'],'%H%M').strftime('%I:%M %p'),
            (datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]),
            (call_record['NAME'] or "Unknown Name")
        ]
        insert_record(query, arguments)
        send_to_dynamodb(call_record['NMBR'], call_record['NAME'])
        print("New record added")
#=================================================================



#=================================================================
# App DB Handler 
#=================================================================
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DB_NAME)
    return db


@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()


def insert_record(query, args=()):
    local_proxy_db = LocalProxy(get_db)
    local_proxy_db.execute(query, args)
    local_proxy_db.commit()
    return

def query_db(query, args=(), one=False):
    cur = get_db().execute(query, args)
    rv = cur.fetchall()
    cur.close()
    return (rv[0] if rv else None) if one else rv
#=================================================================

#=================================================================
# App Send to DynamoDB Handler 
#=================================================================
def send_to_dynamodb(phone_number, name):
    import boto3
    import json

    # Create a DynamoDB client
    #session = boto3.Session(profile_name='default')
    boto3.setup_default_session(profile_name='default')
    dynamodb = boto3.client('dynamodb')

    # Add a new item
    item = {
        'modemID': {'S': MODEM_ID},
        'PhoneNumber': {'S': phone_number},
        'modemTime': {'S': datetime.now().isoformat()},
        'Name': {'S': name}
    }

    response = dynamodb.put_item(
        TableName=DYNAMODB_TABLE_NAME,
        Item=item
    )

    print("DynamoDB Response: ")
    print(response)


#=================================================================
# GET Call Records
#=================================================================
@app.route('/call_details')
def call_details():

    query = 'select S_No, Phone_Number, Modem_Date, Modem_Time, System_Date_Time from Call_Details order by datetime(System_Date_Time) DESC'
    arguments = []

    db_records = query_db(query, arguments)
    call_records = []
    for record in db_records:
        call_records.append(dict(S_No=record[0], Phone_Number=record[1], Modem_Date=record[2], Modem_Time=record[3], System_Date_Time=record[4]))
    
    #print call_records
    #return "test"
    return render_template('call_details.htm',call_records=call_records)
#=================================================================



#=================================================================
# If SQLite DB doesn't exist, create it.
if not os.path.isfile(DB_NAME):
    print("SQLite3 DB doesn't exist. Trying to create DB...")
    init_call_history_DB()

# Main Function
init_modem_settings()

send_to_dynamodb('206-706-1224','20250203T12:12:12')
# Start a new thread to listen to modem data 
data_listener_thread = threading.Thread(target=monitor_modem_line)
data_listener_thread.start()

#with app.app_context():
#    call_details()

if __name__ == '__main__':
    app.run(host= '0.0.0.0')

# Close the Modem Port when the program terminates
atexit.register(close_modem_port)
#=================================================================
