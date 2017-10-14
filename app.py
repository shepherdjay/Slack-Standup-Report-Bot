# Standup Bot by Christina Aiello, 2017
import os
import smtplib
import psycopg2
from slackclient import SlackClient
from time import localtime, strftime
from flask_sqlalchemy import SQLAlchemy
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, request, Response, jsonify, render_template
from wtforms import Form, TextField, TextAreaField, validators, StringField, SubmitField

app = Flask(__name__)
# To do this just using psycopg2: conn = psycopg2.connect(os.environ['DATABASE_URL'])
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ['DATABASE_URL']
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
DB = SQLAlchemy(app)
SCHEDULER = BackgroundScheduler()
SLACK_CLIENT = SlackClient(os.environ['SLACK_BOT_TOKEN'])
STANDUP_MESSAGE_ORIGIN_EMAIL_ADDRESS = "vistaprintdesignexperience@gmail.com"

# Create our database model
class Channel(DB.Model):
    __tablename__ = "channel"
    id = DB.Column(DB.Integer, primary_key=True)
    channel_name = DB.Column(DB.String(120), unique=True)
    standup_hour = DB.Column(DB.Integer)
    standup_minute = DB.Column(DB.Integer)
    message = DB.Column(DB.String(120), unique=False)
    email = DB.Column(DB.String(120), unique=False)
    timestamp = DB.Column(DB.String(120), unique=False)

    def __init__(self, channel_name, standup_hour, standup_minute, message, email, timestamp):
        self.channel_name = channel_name
        self.standup_hour = standup_hour
        self.standup_minute = standup_minute
        self.message = message
        self.email = email
        self.timestamp = timestamp

    def __repr__(self):
        return '<Channel %r>' % self.channel_name


# Our form model
class StandupSignupForm(Form):
    submitted_channel_name = TextField('Channel Name:', validators=[validators.required()])
    standup_hour = TextField('Standup Hour:', validators=[validators.required()])
    standup_minute = TextField('Standup Minute:', validators=[validators.required()])
    message = TextField('Standup Message (Optional. Will use default message if blank.):')
    email = TextField('Email Address to Send Standup Report To (Optional):')


@app.route("/", methods=['GET', 'POST'])
def homepage():
    form = StandupSignupForm(request.form)

    if request.method == 'POST':
        # Get whatever name they gave us for a channel
        submitted_channel_name = request.form['submitted_channel_name']
        standup_hour = request.form['standup_hour']
        standup_minute = request.form['standup_minute']
        message = request.form['message']
        email = request.form['email']
        # If the form field was valid...
        if form.validate():
            # Look for channel in database
            if not DB.session.query(Channel).filter(Channel.channel_name == submitted_channel_name).count():
                # Channel isn't in database. Create our channel object
                channel = Channel(submitted_channel_name, standup_hour, standup_minute, message, email, None)
                # Add it to the database
                DB.session.add(channel)
                DB.session.commit()
                # Adding this additional job to the queue
                SCHEDULER.add_job(standup_call, 'cron', [channel.channel_name, message], day_of_week='mon-fri', hour=standup_hour, minute=standup_minute, id=channel.channel_name + "_standupcall")
                print(create_logging_label() + "Set " + submitted_channel_name + "'s standup time to " + str(standup_hour) + ":" + format_minutes_to_have_zero(standup_minute) + " with standup message: " + message)
                # Set email job if requested
                set_email_job(channel)

            else:
                # If channel is in database, update channel's standup time
                channel = Channel.query.filter_by(channel_name = submitted_channel_name).first()
                channel.standup_hour = standup_hour
                channel.standup_minute = standup_minute
                channel.email = email
                DB.session.commit()
                # Updating this job's timing (need to delete and readd)
                SCHEDULER.remove_job(submitted_channel_name + "_standupcall")
                SCHEDULER.add_job(standup_call, 'cron', [channel.channel_name, message], day_of_week='mon-fri', hour=standup_hour, minute=standup_minute, id=channel.channel_name + "_standupcall")
                print(create_logging_label() + "Updated " + submitted_channel_name + "'s standup time to " + str(standup_hour) + ":" + format_minutes_to_have_zero(standup_minute) + " with standup message: " + message)
                # Set email job if requested
                set_email_job(channel)
        else:
            print(create_logging_label() + "Could not update " + submitted_channel_name + "'s standup time to " + str(standup_hour) + ":" + format_minutes_to_have_zero(standup_minute) + " and message to: " + message + ". Issue was: " + str(request))

    return render_template('homepage.html', form=form)


# Setting the standup schedules for already-existing jobs
# @return nothing
def set_schedules():
    print(create_logging_label() + "Loading previously-submitted standup data.")
    # Get all rows from our table
    channels_with_scheduled_standups = Channel.query.all()
    # Loop through our results
    for channel in channels_with_scheduled_standups:
        # Add a job for each row in the table, sending standup message to channel
        SCHEDULER.add_job(standup_call, 'cron', [channel.channel_name, channel.message], day_of_week='mon-fri', hour=channel.standup_hour, minute=channel.standup_minute, id=channel.channel_name + "_standupcall")
        print(create_logging_label() + "Channel name and time that we scheduled standup call for: " + channel.channel_name + " at " + str(channel.standup_hour) + ":" + format_minutes_to_have_zero(channel.standup_minute) + " with message: " + channel.message)
        # Set email job if requested
        set_email_job(channel)


# Function that triggers the standup call.
# <!channel> will create the @channel call.
# Sets a default message if the user doesn't provide one.
# @param channel_name : name of channel to send standup message to
# @param message : (optional) standup message that's sent to channel
# @return nothing
def standup_call(channel_name, message):
    # Sending our standup message
    result = SLACK_CLIENT.api_call(
      "chat.postMessage",
      channel=str(channel_name),
      text= "<!channel> " + ("Please reply here with your standup status!" if (message == None) else  message),
      username="Standup Bot",
      icon_emoji=":memo:"
    )
    # Evaluating result of call and logging it
    if ("ok" in result):
        print(create_logging_label() + "Standup alert message was sent to " + channel_name)
        print(create_logging_label() + "Result of sending standup message to " + channel_name + " was " + str(result))
        # Getting timestamp for today's standup message for this channel
        channel = Channel.query.filter_by(channel_name = channel_name).first()
        channel.timestamp = result.get("ts")
        DB.session.commit()
    else:
        print(create_logging_label() + "Could not send standup alert message to " + channel_name)


# Used to set the email jobs for any old or new channels with standup messages
# @param channel : Channel object from table (has a channel name, email, etc.
#                  See Channel class above.)
# @return nothing
def set_email_job(channel):
    # See if user wanted standups emailed to them
    if (channel.email):
        # Cancel already existing job if it's there
        if channel.channel_name + "_sendemail" in str(SCHEDULER.get_jobs()):
            SCHEDULER.remove_job(channel.channel_name + "_sendemail")
        # Add a job for each row in the table, sending standup replies to chosen email.
        # Sending this at 1pm every day
        # TODO: Change back to 1pm, not some other random hour and minutes
        SCHEDULER.add_job(get_timestamp_and_send_email, 'cron', [channel.channel_name, channel.email], day_of_week='mon-fri', hour=19, minute=38, id=channel.channel_name + "_sendemail")
        print(create_logging_label() + "Channel that we set email schedule for: " + channel.channel_name)
    else:
        print(create_logging_label() + "Channel " + channel.channel_name + " did not want their standups emailed to them today.")


# Used for logging when actions happen
# @return string with logging time
def create_logging_label():
    return strftime("%Y-%m-%d %H:%M:%S", localtime()) + "| "


# For logging purposes
def format_minutes_to_have_zero(minutes):
    if(int(minutes) < 10):
        return "0" + str(minutes)
    else:
        return str(minutes)


# Emailing standup results to chosen email address.
# Timestamp comes in after we make our standup_call.
# @param channel_name : Name of channel whose standup results we want to email to someone
# @param recipient_email_address : Where to send the standup results to
# @return nothing
def get_timestamp_and_send_email(a_channel_name, recipient_email_address):
    channel = Channel.query.filter_by(channel_name = a_channel_name).first()
    if (channel.timestamp != None):
        # First we need to get all replies to this message:
        standups = get_standup_replies_for_message(channel.timestamp, channel.channel_name)

        # Then we need to send an email with this information
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.ehlo()
        server.starttls()
        server.login(os.environ['USERNAME'] + "@gmail.com", os.environ['PASSWORD'])
        message = 'Subject: {}\n\n{}'.format(a_channel_name + " Standup Report", "standups go here")
        # TODO: Uncomment line below once I structure the input to not have { in it
        # server.sendmail(STANDUP_MESSAGE_ORIGIN_EMAIL_ADDRESS, recipient_email_address, standups)
        server.sendmail(STANDUP_MESSAGE_ORIGIN_EMAIL_ADDRESS, recipient_email_address, a_channel_name)
        server.quit()
        print(create_logging_label() + "Sent " + a_channel_name + "'s standup messages, " + standups + ", to " + recipient_email_address)

        # Finally we need to reset the standup timestamp so we don't get a repeat.
        # We also need to cancel the email job.
        channel.timestamp = None;
        DB.session.commit()
    else:
        # Log that it didn't work
        print(create_logging_label() + "Channel " + a_channel_name + " isn't set up to have standup results sent anywhere because they don't have a timestamp in STANDUP_TIMESTAMP_MAP.")


# Will fetch the standup messages for a channel
# @param timestamp : A channel's standup message's timestamp (acquired via API)
# @return Standup messages in JSON format
def get_standup_replies_for_message(timestamp, channel_name):
    channel_id = get_channel_id_via_name(channel_name)

    # https://api.slack.com/methods/channels.history
    # "To retrieve a single message, specify its ts value as latest, set
    # inclusive to true, and dial your count down to 1"
    result = SLACK_CLIENT.api_call(
      "channels.history",
      token=os.environ['SLACK_BOT_TOKEN'],
      channel="C7A16MBB4", # TODO: change back to channel_id
      latest="1507693919.000178", # TODO: change back to timestamp
      inclusive=True,
      count=1
    )
    if ("ok" in result):
        print(create_logging_label() + "User standup messages: " + str(result))
        standup_results = []
        for standup_status in result.get("messages")[0].get("replies"):
            reply_result = SLACK_CLIENT.api_call(
              "channels.history",
              token=os.environ['SLACK_BOT_TOKEN'],
              channel=channel_id,
              latest=standup_status.get("ts"),
              inclusive=True,
              count=1
            )
            print("reply_result")
            print(reply_result)
            print("reply_result.get(\"messages\"))
            print(reply_result.get("messages"))
            print(reply_result.get("messages")[0])
            print(reply_result.get("messages")[0].get("username"))
            standup_results.append(reply_result.get("messages")[0].get("username") + ": " + reply_result.get("messages")[0].get("text"))
        print(standup_results)
        return standup_results
    else:
        # Log that it didn't work
        print(create_logging_label() + "Tried to retrieve standup results. Could not retrieve standup results for " + channel_name + " due to: " + str(result.error))


# Calls API to get channel ID based on name.
# @param channel_name
# @return channel ID
def get_channel_id_via_name(channel_name):
    channels_list = SLACK_CLIENT.api_call(
      "channels.list",
      token=os.environ['SLACK_BOT_TOKEN']
    )
    print("get_channel_id_via_name " + str(channels_list))
    for channel in channels_list.get("channels"):
        if channel.get("name") == channel_name:
            return channel.get("id")



if __name__ == '__main__':
    app.run(host='0.0.0.0')

get_standup_replies_for_message("test", "test");

# Setting the scheduling
set_schedules()

# Running the scheduling
SCHEDULER.start()

print(create_logging_label() + "Standup bot was started up and scheduled.")
